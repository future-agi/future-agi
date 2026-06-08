from __future__ import annotations

import json
import re
from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured

from accounts.services.onboarding.lifecycle_launch_packets import (
    LIFECYCLE_SEND_COMMAND,
    load_lifecycle_launch_packet,
)
from accounts.services.onboarding.lifecycle_preview_approval import (
    load_lifecycle_preview_approval,
)
from accounts.services.onboarding.lifecycle_send_reports import (
    load_lifecycle_send_dry_run_report_review,
)

SCHEDULE_CONFIG_SOURCE = "onboarding_lifecycle_send_schedule_config"
SCHEDULE_CONFIG_SCHEMA_VERSION = (
    "onboarding-lifecycle-send-schedule-config-2026-05-31.v1"
)
SCHEDULE_CONFIG_DEFAULT_MAX_LIMIT = 100

_DOTENV_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_./:@%+=,-]+$")


@dataclass(frozen=True)
class LifecycleSendScheduleConfigResult:
    env: dict[str, str]
    artifacts: dict[str, dict[str, str]]
    command: dict[str, object]
    enabled: bool

    def to_payload(self):
        return {
            "schema_version": SCHEDULE_CONFIG_SCHEMA_VERSION,
            "source": SCHEDULE_CONFIG_SOURCE,
            "enabled": self.enabled,
            "env": self.env,
            "artifacts": self.artifacts,
            "command": self.command,
        }

    def to_env_text(self):
        lines = [
            "# Validated onboarding lifecycle send schedule config.",
            f"# source={SCHEDULE_CONFIG_SOURCE}",
        ]
        for artifact_name, artifact in self.artifacts.items():
            lines.append(
                f"# {artifact_name}_sha256={artifact['sha256']} path={artifact['path']}"
            )
        lines.extend(f"{key}={_dotenv_value(value)}" for key, value in self.env.items())
        return "\n".join(lines) + "\n"


def _config_error(message):
    return ImproperlyConfigured(
        f"Invalid lifecycle send schedule config inputs: {message}"
    )


def _dotenv_value(value):
    value = "" if value is None else str(value)
    if value and _DOTENV_SAFE_VALUE.match(value):
        return value
    return json.dumps(value)


def _artifact(path, sha_value):
    return {
        "path": str(path),
        "sha256": sha_value,
    }


def _validated_max_limit(limit, max_limit):
    if max_limit is None:
        max_limit = SCHEDULE_CONFIG_DEFAULT_MAX_LIMIT
    try:
        max_limit = int(max_limit)
    except (TypeError, ValueError) as exc:
        raise _config_error("max limit must be an integer.") from exc
    if max_limit < 1:
        raise _config_error("max limit must be greater than zero.")
    if limit > max_limit:
        raise _config_error("send limit cannot exceed max limit.")
    return max_limit


def lifecycle_send_schedule_config_result(
    *,
    approval_manifest_path,
    approval_record_path,
    dry_run_report_path,
    dry_run_report_review_record_path,
    launch_packet_path,
    enable=False,
    max_limit=None,
):
    approval = load_lifecycle_preview_approval(
        approval_manifest_path,
        approval_record_path=approval_record_path,
    )
    if not approval.approval_record_sha256:
        raise _config_error("approval record is required.")

    dry_run_review = load_lifecycle_send_dry_run_report_review(
        report_path=dry_run_report_path,
        review_record_path=dry_run_report_review_record_path,
    )
    report = dry_run_review.report
    if report.command_name != LIFECYCLE_SEND_COMMAND:
        raise _config_error("dry-run report must target lifecycle sends.")
    if report.require_campaign_group_allowlist:
        raise _config_error("scheduled lifecycle sends cannot use group allowlisting.")

    launch_packet = load_lifecycle_launch_packet(
        launch_packet_path,
        command_name=LIFECYCLE_SEND_COMMAND,
        cohort=report.cohort,
        limit=report.limit,
        campaign_group=report.campaign_group,
        user_id=report.user_id,
        workspace_id=report.workspace_id,
        require_campaign_group_allowlist=False,
        approval_manifest_path=approval_manifest_path,
        approval_record_path=approval_record_path,
        dry_run_report_path=dry_run_report_path,
        dry_run_report_review_record_path=dry_run_report_review_record_path,
        approval_manifest_sha256=approval.manifest_sha256,
        approval_record_sha256=approval.approval_record_sha256,
        dry_run_report_sha256=report.sha256,
        dry_run_report_review_record_sha256=dry_run_review.review_record_sha256,
        require_ready=True,
    )

    schedule_max_limit = _validated_max_limit(report.limit, max_limit)
    env = {
        "ONBOARDING_LIFECYCLE_SEND_SCHEDULE_ENABLED": ("true" if enable else "false"),
        "ONBOARDING_LIFECYCLE_SEND_SCHEDULE_COHORT": report.cohort,
        "ONBOARDING_LIFECYCLE_SEND_SCHEDULE_LIMIT": str(report.limit),
        "ONBOARDING_LIFECYCLE_SEND_SCHEDULE_MAX_LIMIT": str(schedule_max_limit),
        "ONBOARDING_LIFECYCLE_SEND_SCHEDULE_CAMPAIGN_GROUP": (
            report.campaign_group or ""
        ),
        "ONBOARDING_LIFECYCLE_SEND_SCHEDULE_USER_ID": report.user_id or "",
        "ONBOARDING_LIFECYCLE_SEND_SCHEDULE_WORKSPACE_ID": (report.workspace_id or ""),
        "ONBOARDING_LIFECYCLE_SEND_APPROVAL_MANIFEST": str(approval_manifest_path),
        "ONBOARDING_LIFECYCLE_SEND_APPROVAL_RECORD": str(approval_record_path),
        "ONBOARDING_LIFECYCLE_SEND_DRY_RUN_REPORT": str(dry_run_report_path),
        "ONBOARDING_LIFECYCLE_SEND_DRY_RUN_REPORT_REVIEW_RECORD": str(
            dry_run_report_review_record_path
        ),
        "ONBOARDING_LIFECYCLE_SEND_LAUNCH_PACKET": str(launch_packet_path),
    }
    artifacts = {
        "approval_manifest": _artifact(
            approval_manifest_path,
            approval.manifest_sha256,
        ),
        "approval_record": _artifact(
            approval_record_path,
            approval.approval_record_sha256,
        ),
        "dry_run_report": _artifact(
            dry_run_report_path,
            report.sha256,
        ),
        "dry_run_report_review_record": _artifact(
            dry_run_report_review_record_path,
            dry_run_review.review_record_sha256,
        ),
        "launch_packet": _artifact(
            launch_packet_path,
            launch_packet.sha256,
        ),
    }
    command = {
        "name": LIFECYCLE_SEND_COMMAND,
        "cohort": report.cohort,
        "limit": report.limit,
        "max_limit": schedule_max_limit,
        "campaign_group": report.campaign_group,
        "user_id": report.user_id,
        "workspace_id": report.workspace_id,
    }
    return LifecycleSendScheduleConfigResult(
        env=env,
        artifacts=artifacts,
        command=command,
        enabled=bool(enable),
    )
