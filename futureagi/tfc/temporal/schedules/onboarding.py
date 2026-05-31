from typing import Any

from django.conf import settings

from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig


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


def _skipped(job: str, reason: str = "cloud_jobs_disabled") -> dict[str, Any]:
    return {"job": job, "status": "skipped", "reason": reason}


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
]


__all__ = [
    "ONBOARDING_SCHEDULES",
    "run_onboarding_lifecycle_dry_run_scheduled_activity",
    "run_onboarding_activation_export_scheduled_activity",
    "deliver_onboarding_activation_exports_scheduled_activity",
    "import_onboarding_activation_fact_lifecycle_scheduled_activity",
]
