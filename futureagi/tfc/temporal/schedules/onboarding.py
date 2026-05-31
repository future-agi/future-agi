from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig

LIFECYCLE_SEND_SCHEDULE_MAX_LIMIT_DEFAULT = 100


def _cloud_jobs_enabled() -> bool:
    if not getattr(settings, "ONBOARDING_CLOUD_ACTIVATION_JOBS_ENABLED", False):
        return False
    try:
        from ee.usage.deployment import DeploymentMode
    except ImportError:
        return False
    return bool(DeploymentMode.is_cloud())


def _setting_int(name: str, default: int) -> int:
    value = getattr(settings, name, default)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, value)


def _setting_str(name: str, default: str = "") -> str:
    return str(getattr(settings, name, default) or "").strip()


def _setting_bool(name: str, default: bool = False) -> bool:
    return bool(getattr(settings, name, default))


def _activation_export_delivery_skip_reason() -> str | None:
    endpoint_url = str(
        getattr(settings, "ONBOARDING_ACTIVATION_EXPORT_DELIVERY_URL", "") or ""
    )
    shared_secret = str(
        getattr(settings, "ONBOARDING_ACTIVATION_EXPORT_SHARED_SECRET", "") or ""
    )
    if not endpoint_url:
        return "activation_export_delivery_url_missing"
    if not endpoint_url.startswith("https://"):
        return "activation_export_delivery_url_invalid"
    if not shared_secret:
        return "activation_export_shared_secret_missing"
    return None


def _lifecycle_send_schedule_settings() -> dict[str, Any]:
    return {
        "cohort": _setting_str("ONBOARDING_LIFECYCLE_SEND_SCHEDULE_COHORT", "internal"),
        "limit": _setting_int("ONBOARDING_LIFECYCLE_SEND_SCHEDULE_LIMIT", 25),
        "max_limit": _setting_int(
            "ONBOARDING_LIFECYCLE_SEND_SCHEDULE_MAX_LIMIT",
            LIFECYCLE_SEND_SCHEDULE_MAX_LIMIT_DEFAULT,
        ),
        "campaign_group": _setting_str(
            "ONBOARDING_LIFECYCLE_SEND_SCHEDULE_CAMPAIGN_GROUP"
        )
        or None,
        "user_id": _setting_str("ONBOARDING_LIFECYCLE_SEND_SCHEDULE_USER_ID") or None,
        "workspace_id": _setting_str("ONBOARDING_LIFECYCLE_SEND_SCHEDULE_WORKSPACE_ID")
        or None,
        "approval_manifest_path": _setting_str(
            "ONBOARDING_LIFECYCLE_SEND_APPROVAL_MANIFEST"
        ),
        "approval_record_path": _setting_str(
            "ONBOARDING_LIFECYCLE_SEND_APPROVAL_RECORD"
        ),
        "dry_run_report_path": _setting_str("ONBOARDING_LIFECYCLE_SEND_DRY_RUN_REPORT"),
        "dry_run_report_review_record_path": _setting_str(
            "ONBOARDING_LIFECYCLE_SEND_DRY_RUN_REPORT_REVIEW_RECORD"
        ),
        "launch_packet_path": _setting_str("ONBOARDING_LIFECYCLE_SEND_LAUNCH_PACKET"),
    }


def _lifecycle_send_schedule_skip_reason(config: dict[str, Any]) -> str | None:
    if not _setting_bool("ONBOARDING_LIFECYCLE_SEND_SCHEDULE_ENABLED", False):
        return "lifecycle_send_schedule_disabled"
    if config["cohort"] not in {"internal", "beta"}:
        return "lifecycle_send_cohort_invalid"
    if config["limit"] > config["max_limit"]:
        return "lifecycle_send_limit_too_high"
    required_paths = {
        "approval_manifest_path": "lifecycle_send_approval_manifest_missing",
        "approval_record_path": "lifecycle_send_approval_record_missing",
        "dry_run_report_path": "lifecycle_send_dry_run_report_missing",
        "dry_run_report_review_record_path": (
            "lifecycle_send_dry_run_report_review_missing"
        ),
        "launch_packet_path": "lifecycle_send_launch_packet_missing",
    }
    for key, reason in required_paths.items():
        if not config[key]:
            return reason
    return None


def _skipped(
    job: str,
    reason: str = "cloud_jobs_disabled",
    **details,
) -> dict[str, Any]:
    payload = {"job": job, "status": "skipped", "reason": reason}
    if details:
        payload.update(details)
    return payload


def _completed(job: str, result) -> dict[str, Any]:
    return {"job": job, "status": "completed", "result": result.to_payload()}


@temporal_activity(time_limit=900, queue="tasks_s")
def run_onboarding_lifecycle_dry_run_scheduled_activity():
    if not _cloud_jobs_enabled():
        return _skipped("lifecycle_dry_run")
    from accounts.services.onboarding.lifecycle_jobs import (
        run_onboarding_lifecycle_dry_run,
    )

    result = run_onboarding_lifecycle_dry_run(
        limit=_setting_int("ONBOARDING_LIFECYCLE_DRY_RUN_SCHEDULE_LIMIT", 500),
        source="scheduled_lifecycle_dry_run",
        write=True,
    )
    return _completed("lifecycle_dry_run", result)


@temporal_activity(time_limit=900, queue="tasks_s")
def run_onboarding_activation_export_scheduled_activity():
    if not _cloud_jobs_enabled():
        return _skipped("activation_export")
    from accounts.services.onboarding.activation_exporter import (
        run_onboarding_activation_export,
    )

    result = run_onboarding_activation_export(
        limit=_setting_int("ONBOARDING_ACTIVATION_EXPORT_SCHEDULE_LIMIT", 500),
        source="scheduled_activation_export",
        write=True,
    )
    return _completed("activation_export", result)


@temporal_activity(time_limit=600, queue="tasks_s")
def deliver_onboarding_activation_exports_scheduled_activity():
    if not _cloud_jobs_enabled():
        return _skipped("activation_export_delivery")
    if reason := _activation_export_delivery_skip_reason():
        return _skipped("activation_export_delivery", reason=reason)
    from accounts.services.onboarding.activation_export_delivery import (
        run_onboarding_activation_export_delivery,
    )

    result = run_onboarding_activation_export_delivery(
        limit=_setting_int("ONBOARDING_ACTIVATION_EXPORT_DELIVERY_SCHEDULE_LIMIT", 250),
        dry_run=False,
        retry_failed=False,
    )
    return _completed("activation_export_delivery", result)


@temporal_activity(time_limit=900, queue="tasks_s")
def import_onboarding_activation_fact_lifecycle_scheduled_activity():
    if not _cloud_jobs_enabled():
        return _skipped("activation_fact_lifecycle_import")
    from accounts.services.onboarding.activation_fact_lifecycle import (
        import_activation_fact_lifecycle_evaluations,
    )

    result = import_activation_fact_lifecycle_evaluations(
        limit=_setting_int(
            "ONBOARDING_ACTIVATION_FACT_LIFECYCLE_IMPORT_SCHEDULE_LIMIT",
            500,
        ),
        write=True,
    )
    return _completed("activation_fact_lifecycle_import", result)


@temporal_activity(time_limit=900, queue="tasks_s")
def run_onboarding_lifecycle_send_scheduled_activity():
    if not _cloud_jobs_enabled():
        return _skipped("lifecycle_send")
    config = _lifecycle_send_schedule_settings()
    if reason := _lifecycle_send_schedule_skip_reason(config):
        return _skipped("lifecycle_send", reason=reason)

    try:
        from accounts.services.onboarding.lifecycle_launch_packets import (
            load_lifecycle_launch_packet,
        )
        from accounts.services.onboarding.lifecycle_preview_approval import (
            load_lifecycle_preview_approval,
        )
        from accounts.services.onboarding.lifecycle_send_reports import (
            load_lifecycle_send_dry_run_report_review,
        )
        from accounts.services.onboarding.lifecycle_sender import (
            send_limited_onboarding_lifecycle_batch,
        )

        preview_approval = load_lifecycle_preview_approval(
            config["approval_manifest_path"],
            approval_record_path=config["approval_record_path"],
        )
        dry_run_report_review = load_lifecycle_send_dry_run_report_review(
            report_path=config["dry_run_report_path"],
            review_record_path=config["dry_run_report_review_record_path"],
            command_name="run_onboarding_lifecycle_send",
            cohort=config["cohort"],
            limit=config["limit"],
            campaign_group=config["campaign_group"],
            user_id=config["user_id"],
            workspace_id=config["workspace_id"],
            approval_manifest_sha256=preview_approval.manifest_sha256,
            approval_record_sha256=preview_approval.approval_record_sha256,
        )
        launch_packet = load_lifecycle_launch_packet(
            config["launch_packet_path"],
            command_name="run_onboarding_lifecycle_send",
            cohort=config["cohort"],
            limit=config["limit"],
            campaign_group=config["campaign_group"],
            user_id=config["user_id"],
            workspace_id=config["workspace_id"],
            require_campaign_group_allowlist=False,
            approval_manifest_path=config["approval_manifest_path"],
            approval_record_path=config["approval_record_path"],
            dry_run_report_path=config["dry_run_report_path"],
            dry_run_report_review_record_path=(
                config["dry_run_report_review_record_path"]
            ),
            approval_manifest_sha256=preview_approval.manifest_sha256,
            approval_record_sha256=preview_approval.approval_record_sha256,
            dry_run_report_sha256=dry_run_report_review.report.sha256,
            dry_run_report_review_record_sha256=(
                dry_run_report_review.review_record_sha256
            ),
            require_ready=True,
        )
    except ImproperlyConfigured as exc:
        return _skipped(
            "lifecycle_send",
            reason="lifecycle_send_artifact_invalid",
            error=str(exc),
        )

    result = send_limited_onboarding_lifecycle_batch(
        cohort=config["cohort"],
        limit=config["limit"],
        campaign_group=config["campaign_group"],
        user_id=config["user_id"],
        workspace_id=config["workspace_id"],
        dry_run=False,
        preview_approval=preview_approval,
        dry_run_report_review=dry_run_report_review,
        launch_packet=launch_packet,
    )
    return _completed("lifecycle_send", result)


ONBOARDING_SCHEDULES: list[ScheduleConfig] = [
    ScheduleConfig(
        schedule_id="onboarding-lifecycle-dry-run",
        activity_name="run_onboarding_lifecycle_dry_run_scheduled_activity",
        interval_seconds=3600,
        queue="tasks_s",
        description="Evaluate onboarding lifecycle candidates for review.",
    ),
    ScheduleConfig(
        schedule_id="onboarding-activation-export",
        activity_name="run_onboarding_activation_export_scheduled_activity",
        interval_seconds=900,
        queue="tasks_s",
        description="Write paid-cloud activation facts into the export outbox.",
    ),
    ScheduleConfig(
        schedule_id="onboarding-activation-export-delivery",
        activity_name="deliver_onboarding_activation_exports_scheduled_activity",
        interval_seconds=300,
        queue="tasks_s",
        description="Deliver ready paid-cloud activation export rows.",
    ),
    ScheduleConfig(
        schedule_id="onboarding-activation-fact-lifecycle-import",
        activity_name="import_onboarding_activation_fact_lifecycle_scheduled_activity",
        interval_seconds=900,
        queue="tasks_s",
        description="Import accepted activation fact receipts into lifecycle review rows.",
    ),
    ScheduleConfig(
        schedule_id="onboarding-lifecycle-send",
        activity_name="run_onboarding_lifecycle_send_scheduled_activity",
        interval_seconds=3600,
        queue="tasks_s",
        description="Send approved onboarding lifecycle emails to an allowlisted cohort.",
    ),
]


__all__ = [
    "ONBOARDING_SCHEDULES",
    "run_onboarding_lifecycle_dry_run_scheduled_activity",
    "run_onboarding_activation_export_scheduled_activity",
    "deliver_onboarding_activation_exports_scheduled_activity",
    "import_onboarding_activation_fact_lifecycle_scheduled_activity",
    "run_onboarding_lifecycle_send_scheduled_activity",
]
