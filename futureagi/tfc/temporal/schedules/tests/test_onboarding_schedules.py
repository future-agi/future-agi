import sys

from django.core.exceptions import ImproperlyConfigured

from tfc.temporal.common.registry import TEMPORAL_ACTIVITY_MODULES
from tfc.temporal.schedules import ALL_SCHEDULES
from tfc.temporal.schedules.onboarding import (
    ONBOARDING_SCHEDULES,
    deliver_onboarding_activation_exports_scheduled_activity,
    import_onboarding_activation_fact_lifecycle_scheduled_activity,
    run_onboarding_activation_export_scheduled_activity,
    run_onboarding_lifecycle_dry_run_scheduled_activity,
    run_onboarding_lifecycle_send_scheduled_activity,
)


class _Result:
    def __init__(self, payload):
        self._payload = payload

    def to_payload(self):
        return self._payload


class _PreviewApproval:
    manifest_sha256 = "a" * 64
    approval_record_sha256 = "b" * 64


class _DryRunReport:
    sha256 = "c" * 64


class _DryRunReportReview:
    report = _DryRunReport()
    review_record_sha256 = "d" * 64


class _LaunchPacket:
    sha256 = "e" * 64


def _run_all_scheduled_activities():
    return [
        run_onboarding_lifecycle_dry_run_scheduled_activity(),
        run_onboarding_activation_export_scheduled_activity(),
        deliver_onboarding_activation_exports_scheduled_activity(),
        import_onboarding_activation_fact_lifecycle_scheduled_activity(),
        run_onboarding_lifecycle_send_scheduled_activity(),
    ]


def test_onboarding_schedules_are_in_global_schedule_registry():
    onboarding_ids = {schedule.schedule_id for schedule in ONBOARDING_SCHEDULES}
    global_ids = {schedule.schedule_id for schedule in ALL_SCHEDULES}

    assert onboarding_ids == {
        "onboarding-lifecycle-dry-run",
        "onboarding-activation-export",
        "onboarding-activation-export-delivery",
        "onboarding-activation-fact-lifecycle-import",
        "onboarding-lifecycle-send",
    }
    assert onboarding_ids <= global_ids
    assert {schedule.queue for schedule in ONBOARDING_SCHEDULES} == {"tasks_s"}


def test_onboarding_schedule_activities_are_imported_for_workers():
    assert "tfc.temporal.schedules.onboarding" in TEMPORAL_ACTIVITY_MODULES


def test_onboarding_scheduled_activities_skip_by_default():
    results = _run_all_scheduled_activities()

    assert {result["status"] for result in results} == {"skipped"}
    assert {result["reason"] for result in results} == {"cloud_jobs_disabled"}


def test_onboarding_scheduled_activities_skip_when_cloud_jobs_disabled(settings):
    settings.ONBOARDING_CLOUD_ACTIVATION_JOBS_ENABLED = False

    results = _run_all_scheduled_activities()

    assert {result["status"] for result in results} == {"skipped"}
    assert {result["reason"] for result in results} == {"cloud_jobs_disabled"}


def test_onboarding_scheduled_activities_skip_when_deployment_is_not_cloud(
    monkeypatch,
    settings,
):
    settings.ONBOARDING_CLOUD_ACTIVATION_JOBS_ENABLED = True
    from ee.usage.deployment import DeploymentMode

    monkeypatch.setattr(DeploymentMode, "is_cloud", staticmethod(lambda: False))

    results = _run_all_scheduled_activities()

    assert {result["status"] for result in results} == {"skipped"}
    assert {result["reason"] for result in results} == {"cloud_jobs_disabled"}


def test_onboarding_scheduled_activities_skip_when_deployment_module_is_missing(
    monkeypatch,
    settings,
):
    settings.ONBOARDING_CLOUD_ACTIVATION_JOBS_ENABLED = True
    monkeypatch.setitem(sys.modules, "ee.usage.deployment", None)

    results = _run_all_scheduled_activities()

    assert {result["status"] for result in results} == {"skipped"}
    assert {result["reason"] for result in results} == {"cloud_jobs_disabled"}


def test_onboarding_lifecycle_dry_run_schedule_calls_service(monkeypatch, settings):
    calls = []
    settings.ONBOARDING_LIFECYCLE_DRY_RUN_SCHEDULE_LIMIT = "17"
    monkeypatch.setattr(
        "tfc.temporal.schedules.onboarding._cloud_jobs_enabled",
        lambda: True,
    )

    def _run(**kwargs):
        calls.append(kwargs)
        return _Result({"evaluated": 3})

    monkeypatch.setattr(
        "accounts.services.onboarding.lifecycle_jobs.run_onboarding_lifecycle_dry_run",
        _run,
    )

    result = run_onboarding_lifecycle_dry_run_scheduled_activity()

    assert result == {
        "job": "lifecycle_dry_run",
        "status": "completed",
        "result": {"evaluated": 3},
    }
    assert calls == [
        {
            "limit": 17,
            "source": "scheduled_lifecycle_dry_run",
            "write": True,
        }
    ]


def test_onboarding_activation_export_schedule_calls_service(monkeypatch, settings):
    calls = []
    settings.ONBOARDING_ACTIVATION_EXPORT_SCHEDULE_LIMIT = 23
    monkeypatch.setattr(
        "tfc.temporal.schedules.onboarding._cloud_jobs_enabled",
        lambda: True,
    )

    def _run(**kwargs):
        calls.append(kwargs)
        return _Result({"written": 5})

    monkeypatch.setattr(
        "accounts.services.onboarding.activation_exporter.run_onboarding_activation_export",
        _run,
    )

    result = run_onboarding_activation_export_scheduled_activity()

    assert result == {
        "job": "activation_export",
        "status": "completed",
        "result": {"written": 5},
    }
    assert calls == [
        {
            "limit": 23,
            "source": "scheduled_activation_export",
            "write": True,
        }
    ]


def test_onboarding_activation_export_delivery_schedule_calls_service(
    monkeypatch,
    settings,
):
    calls = []
    settings.ONBOARDING_ACTIVATION_EXPORT_DELIVERY_URL = (
        "https://activation.example/receive"
    )
    settings.ONBOARDING_ACTIVATION_EXPORT_SHARED_SECRET = "test-secret"
    settings.ONBOARDING_ACTIVATION_EXPORT_DELIVERY_SCHEDULE_LIMIT = 29
    monkeypatch.setattr(
        "tfc.temporal.schedules.onboarding._cloud_jobs_enabled",
        lambda: True,
    )

    def _run(**kwargs):
        calls.append(kwargs)
        return _Result({"delivered": 8})

    monkeypatch.setattr(
        "accounts.services.onboarding.activation_export_delivery.run_onboarding_activation_export_delivery",
        _run,
    )

    result = deliver_onboarding_activation_exports_scheduled_activity()

    assert result == {
        "job": "activation_export_delivery",
        "status": "completed",
        "result": {"delivered": 8},
    }
    assert calls == [
        {
            "limit": 29,
            "dry_run": False,
            "retry_failed": False,
        }
    ]


def test_onboarding_activation_export_delivery_schedule_skips_missing_config(
    monkeypatch,
    settings,
):
    settings.ONBOARDING_ACTIVATION_EXPORT_DELIVERY_URL = ""
    settings.ONBOARDING_ACTIVATION_EXPORT_SHARED_SECRET = "test-secret"
    monkeypatch.setattr(
        "tfc.temporal.schedules.onboarding._cloud_jobs_enabled",
        lambda: True,
    )

    def _run(**kwargs):
        raise AssertionError("delivery service should not run without config")

    monkeypatch.setattr(
        "accounts.services.onboarding.activation_export_delivery.run_onboarding_activation_export_delivery",
        _run,
    )

    result = deliver_onboarding_activation_exports_scheduled_activity()

    assert result == {
        "job": "activation_export_delivery",
        "status": "skipped",
        "reason": "activation_export_delivery_url_missing",
    }


def test_onboarding_activation_export_delivery_schedule_skips_invalid_url(
    monkeypatch,
    settings,
):
    settings.ONBOARDING_ACTIVATION_EXPORT_DELIVERY_URL = "http://activation.example"
    settings.ONBOARDING_ACTIVATION_EXPORT_SHARED_SECRET = "test-secret"
    monkeypatch.setattr(
        "tfc.temporal.schedules.onboarding._cloud_jobs_enabled",
        lambda: True,
    )

    result = deliver_onboarding_activation_exports_scheduled_activity()

    assert result == {
        "job": "activation_export_delivery",
        "status": "skipped",
        "reason": "activation_export_delivery_url_invalid",
    }


def test_onboarding_activation_export_delivery_schedule_skips_missing_secret(
    monkeypatch,
    settings,
):
    settings.ONBOARDING_ACTIVATION_EXPORT_DELIVERY_URL = (
        "https://activation.example/receive"
    )
    settings.ONBOARDING_ACTIVATION_EXPORT_SHARED_SECRET = ""
    monkeypatch.setattr(
        "tfc.temporal.schedules.onboarding._cloud_jobs_enabled",
        lambda: True,
    )

    result = deliver_onboarding_activation_exports_scheduled_activity()

    assert result == {
        "job": "activation_export_delivery",
        "status": "skipped",
        "reason": "activation_export_shared_secret_missing",
    }


def test_onboarding_activation_fact_import_schedule_calls_service(
    monkeypatch,
    settings,
):
    calls = []
    settings.ONBOARDING_ACTIVATION_FACT_LIFECYCLE_IMPORT_SCHEDULE_LIMIT = 31
    monkeypatch.setattr(
        "tfc.temporal.schedules.onboarding._cloud_jobs_enabled",
        lambda: True,
    )

    def _run(**kwargs):
        calls.append(kwargs)
        return _Result({"imported": 13})

    monkeypatch.setattr(
        "accounts.services.onboarding.activation_fact_lifecycle.import_activation_fact_lifecycle_evaluations",
        _run,
    )

    result = import_onboarding_activation_fact_lifecycle_scheduled_activity()

    assert result == {
        "job": "activation_fact_lifecycle_import",
        "status": "completed",
        "result": {"imported": 13},
    }
    assert calls == [{"limit": 31, "write": True}]


def test_onboarding_lifecycle_send_schedule_skips_when_disabled(
    monkeypatch,
    settings,
):
    settings.ONBOARDING_LIFECYCLE_SEND_SCHEDULE_ENABLED = False
    monkeypatch.setattr(
        "tfc.temporal.schedules.onboarding._cloud_jobs_enabled",
        lambda: True,
    )

    result = run_onboarding_lifecycle_send_scheduled_activity()

    assert result == {
        "job": "lifecycle_send",
        "status": "skipped",
        "reason": "lifecycle_send_schedule_disabled",
    }


def test_onboarding_lifecycle_send_schedule_skips_missing_artifacts(
    monkeypatch,
    settings,
):
    settings.ONBOARDING_LIFECYCLE_SEND_SCHEDULE_ENABLED = True
    settings.ONBOARDING_LIFECYCLE_SEND_APPROVAL_MANIFEST = ""
    monkeypatch.setattr(
        "tfc.temporal.schedules.onboarding._cloud_jobs_enabled",
        lambda: True,
    )

    result = run_onboarding_lifecycle_send_scheduled_activity()

    assert result == {
        "job": "lifecycle_send",
        "status": "skipped",
        "reason": "lifecycle_send_approval_manifest_missing",
    }


def test_onboarding_lifecycle_send_schedule_skips_limit_above_max(
    monkeypatch,
    settings,
):
    settings.ONBOARDING_LIFECYCLE_SEND_SCHEDULE_ENABLED = True
    settings.ONBOARDING_LIFECYCLE_SEND_SCHEDULE_LIMIT = 101
    settings.ONBOARDING_LIFECYCLE_SEND_SCHEDULE_MAX_LIMIT = 100
    settings.ONBOARDING_LIFECYCLE_SEND_APPROVAL_MANIFEST = "/tmp/manifest.json"
    settings.ONBOARDING_LIFECYCLE_SEND_APPROVAL_RECORD = "/tmp/approval.json"
    settings.ONBOARDING_LIFECYCLE_SEND_DRY_RUN_REPORT = "/tmp/report.json"
    settings.ONBOARDING_LIFECYCLE_SEND_DRY_RUN_REPORT_REVIEW_RECORD = (
        "/tmp/report-review.json"
    )
    settings.ONBOARDING_LIFECYCLE_SEND_LAUNCH_PACKET = "/tmp/launch-packet.json"
    monkeypatch.setattr(
        "tfc.temporal.schedules.onboarding._cloud_jobs_enabled",
        lambda: True,
    )

    result = run_onboarding_lifecycle_send_scheduled_activity()

    assert result == {
        "job": "lifecycle_send",
        "status": "skipped",
        "reason": "lifecycle_send_limit_too_high",
    }


def test_onboarding_lifecycle_send_schedule_skips_invalid_artifacts(
    monkeypatch,
    settings,
):
    settings.ONBOARDING_LIFECYCLE_SEND_SCHEDULE_ENABLED = True
    settings.ONBOARDING_LIFECYCLE_SEND_APPROVAL_MANIFEST = "/tmp/manifest.json"
    settings.ONBOARDING_LIFECYCLE_SEND_APPROVAL_RECORD = "/tmp/approval.json"
    settings.ONBOARDING_LIFECYCLE_SEND_DRY_RUN_REPORT = "/tmp/report.json"
    settings.ONBOARDING_LIFECYCLE_SEND_DRY_RUN_REPORT_REVIEW_RECORD = (
        "/tmp/report-review.json"
    )
    settings.ONBOARDING_LIFECYCLE_SEND_LAUNCH_PACKET = "/tmp/launch-packet.json"
    monkeypatch.setattr(
        "tfc.temporal.schedules.onboarding._cloud_jobs_enabled",
        lambda: True,
    )

    def _load_preview(*_args, **_kwargs):
        raise ImproperlyConfigured("approval mismatch")

    monkeypatch.setattr(
        "accounts.services.onboarding.lifecycle_preview_approval.load_lifecycle_preview_approval",
        _load_preview,
    )

    result = run_onboarding_lifecycle_send_scheduled_activity()

    assert result == {
        "job": "lifecycle_send",
        "status": "skipped",
        "reason": "lifecycle_send_artifact_invalid",
        "error": "approval mismatch",
    }


def test_onboarding_lifecycle_send_schedule_calls_service(monkeypatch, settings):
    calls = []
    settings.ONBOARDING_LIFECYCLE_SEND_SCHEDULE_ENABLED = True
    settings.ONBOARDING_LIFECYCLE_SEND_SCHEDULE_COHORT = "beta"
    settings.ONBOARDING_LIFECYCLE_SEND_SCHEDULE_LIMIT = "11"
    settings.ONBOARDING_LIFECYCLE_SEND_SCHEDULE_MAX_LIMIT = 25
    settings.ONBOARDING_LIFECYCLE_SEND_SCHEDULE_CAMPAIGN_GROUP = "prompt"
    settings.ONBOARDING_LIFECYCLE_SEND_SCHEDULE_USER_ID = "user-1"
    settings.ONBOARDING_LIFECYCLE_SEND_SCHEDULE_WORKSPACE_ID = "workspace-1"
    settings.ONBOARDING_LIFECYCLE_SEND_APPROVAL_MANIFEST = "/tmp/manifest.json"
    settings.ONBOARDING_LIFECYCLE_SEND_APPROVAL_RECORD = "/tmp/approval.json"
    settings.ONBOARDING_LIFECYCLE_SEND_DRY_RUN_REPORT = "/tmp/report.json"
    settings.ONBOARDING_LIFECYCLE_SEND_DRY_RUN_REPORT_REVIEW_RECORD = (
        "/tmp/report-review.json"
    )
    settings.ONBOARDING_LIFECYCLE_SEND_LAUNCH_PACKET = "/tmp/launch-packet.json"
    monkeypatch.setattr(
        "tfc.temporal.schedules.onboarding._cloud_jobs_enabled",
        lambda: True,
    )

    def _load_preview(*args, **kwargs):
        calls.append(("preview", args, kwargs))
        return _PreviewApproval()

    def _load_report_review(**kwargs):
        calls.append(("dry_run_review", (), kwargs))
        return _DryRunReportReview()

    def _load_launch_packet(*args, **kwargs):
        calls.append(("launch_packet", args, kwargs))
        return _LaunchPacket()

    def _send_batch(**kwargs):
        calls.append(("send", (), kwargs))
        return _Result({"sent": 2, "suppressed": 1})

    monkeypatch.setattr(
        "accounts.services.onboarding.lifecycle_preview_approval.load_lifecycle_preview_approval",
        _load_preview,
    )
    monkeypatch.setattr(
        "accounts.services.onboarding.lifecycle_send_reports.load_lifecycle_send_dry_run_report_review",
        _load_report_review,
    )
    monkeypatch.setattr(
        "accounts.services.onboarding.lifecycle_launch_packets.load_lifecycle_launch_packet",
        _load_launch_packet,
    )
    monkeypatch.setattr(
        "accounts.services.onboarding.lifecycle_sender.send_limited_onboarding_lifecycle_batch",
        _send_batch,
    )

    result = run_onboarding_lifecycle_send_scheduled_activity()

    assert result == {
        "job": "lifecycle_send",
        "status": "completed",
        "result": {"sent": 2, "suppressed": 1},
    }
    assert calls[0] == (
        "preview",
        ("/tmp/manifest.json",),
        {"approval_record_path": "/tmp/approval.json"},
    )
    assert calls[1] == (
        "dry_run_review",
        (),
        {
            "report_path": "/tmp/report.json",
            "review_record_path": "/tmp/report-review.json",
            "command_name": "run_onboarding_lifecycle_send",
            "cohort": "beta",
            "limit": 11,
            "campaign_group": "prompt",
            "user_id": "user-1",
            "workspace_id": "workspace-1",
            "approval_manifest_sha256": "a" * 64,
            "approval_record_sha256": "b" * 64,
        },
    )
    assert calls[2] == (
        "launch_packet",
        ("/tmp/launch-packet.json",),
        {
            "command_name": "run_onboarding_lifecycle_send",
            "cohort": "beta",
            "limit": 11,
            "campaign_group": "prompt",
            "user_id": "user-1",
            "workspace_id": "workspace-1",
            "require_campaign_group_allowlist": False,
            "approval_manifest_path": "/tmp/manifest.json",
            "approval_record_path": "/tmp/approval.json",
            "dry_run_report_path": "/tmp/report.json",
            "dry_run_report_review_record_path": "/tmp/report-review.json",
            "approval_manifest_sha256": "a" * 64,
            "approval_record_sha256": "b" * 64,
            "dry_run_report_sha256": "c" * 64,
            "dry_run_report_review_record_sha256": "d" * 64,
            "require_ready": True,
        },
    )
    send_name, send_args, send_kwargs = calls[3]
    assert send_name == "send"
    assert send_args == ()
    assert {
        key: value
        for key, value in send_kwargs.items()
        if key
        not in {
            "preview_approval",
            "dry_run_report_review",
            "launch_packet",
        }
    } == {
        "cohort": "beta",
        "limit": 11,
        "campaign_group": "prompt",
        "user_id": "user-1",
        "workspace_id": "workspace-1",
        "dry_run": False,
    }
    assert isinstance(send_kwargs["preview_approval"], _PreviewApproval)
    assert isinstance(send_kwargs["dry_run_report_review"], _DryRunReportReview)
    assert isinstance(send_kwargs["launch_packet"], _LaunchPacket)
