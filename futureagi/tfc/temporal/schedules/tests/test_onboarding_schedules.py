import sys

from tfc.temporal.common.registry import TEMPORAL_ACTIVITY_MODULES
from tfc.temporal.schedules import ALL_SCHEDULES
from tfc.temporal.schedules.onboarding import (
    ONBOARDING_SCHEDULES,
    deliver_onboarding_activation_exports_scheduled_activity,
    import_onboarding_activation_fact_lifecycle_scheduled_activity,
    run_onboarding_activation_export_scheduled_activity,
    run_onboarding_lifecycle_dry_run_scheduled_activity,
)


class _Result:
    def __init__(self, payload):
        self._payload = payload

    def to_payload(self):
        return self._payload


def _run_all_scheduled_activities():
    return [
        run_onboarding_lifecycle_dry_run_scheduled_activity(),
        run_onboarding_activation_export_scheduled_activity(),
        deliver_onboarding_activation_exports_scheduled_activity(),
        import_onboarding_activation_fact_lifecycle_scheduled_activity(),
    ]


def test_onboarding_schedules_are_in_global_schedule_registry():
    onboarding_ids = {schedule.schedule_id for schedule in ONBOARDING_SCHEDULES}
    global_ids = {schedule.schedule_id for schedule in ALL_SCHEDULES}

    assert onboarding_ids == {
        "onboarding-lifecycle-dry-run",
        "onboarding-activation-export",
        "onboarding-activation-export-delivery",
        "onboarding-activation-fact-lifecycle-import",
    }
    assert onboarding_ids <= global_ids
    assert {schedule.queue for schedule in ONBOARDING_SCHEDULES} == {"tasks_s"}


def test_onboarding_schedule_activities_are_imported_for_workers():
    assert "tfc.temporal.schedules.onboarding" in TEMPORAL_ACTIVITY_MODULES


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
