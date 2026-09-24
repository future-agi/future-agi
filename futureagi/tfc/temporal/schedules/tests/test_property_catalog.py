from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from temporalio.client import ScheduleOverlapPolicy

from tfc.temporal.common.registry import (
    TEMPORAL_ACTIVITY_MODULES,
    get_activities_for_queue,
    get_workflows_for_queue,
)
from tfc.temporal.drop_in import TaskRunnerWorkflow
from tfc.temporal.property_catalog_queue import (
    DEFAULT_PROPERTY_CATALOG_TASK_QUEUE,
    configured_property_catalog_task_queue,
    workspace_property_catalog_task_queue,
)
from tfc.temporal.schedules import property_catalog
from tracer.services.clickhouse.v2.property_catalog.dev_rollout import (
    DEV_ROLLOUT_ACK,
    DEV_STANDARD_MAX_WALL_MS,
)
from tracer.services.clickhouse.v2.property_catalog.reconciler import ReconcileMode

ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE_A = "22222222-2222-4222-8222-222222222222"
WORKSPACE_B = "33333333-3333-4333-8333-333333333333"


def _settings(**overrides: object) -> SimpleNamespace:
    values = {
        "CLOUD_DEPLOYMENT": "DEV",
        "ENV_TYPE": "development",
        "PROPERTY_CATALOG_DEV_ACKNOWLEDGEMENT": DEV_ROLLOUT_ACK,
        "PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT": "DEV",
        "PROPERTY_CATALOG_DEV_ENVIRONMENT": "development",
        "PROPERTY_CATALOG_DEV_IDENTITY": "dev:futureagi-us",
        "PROPERTY_CATALOG_DEV_MAX_WALL_MS": 100_000,
        "PROPERTY_CATALOG_DEV_ORGANIZATION_ID": ORG,
        "PROPERTY_CATALOG_DEV_RECONCILE_ENABLED": True,
        "PROPERTY_CATALOG_DEV_SCHEDULED_RECONCILE_WALL_MS": 1_200_000,
        "PROPERTY_CATALOG_DEV_RUNTIME_FACTORY": (
            property_catalog.CHECKED_IN_DEV_RUNTIME_FACTORY_PATH
        ),
        "PROPERTY_CATALOG_DEV_SOURCE_DATABASE": "futureagi",
        "PROPERTY_CATALOG_DEV_TARGET_DATABASE": "property_catalog_dev_clean",
        "PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST": (WORKSPACE_A,),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_property_catalog_schedule_is_default_off() -> None:
    disabled = _settings(
        ENV_TYPE="production",
        CLOUD_DEPLOYMENT="US",
        PROPERTY_CATALOG_DEV_RECONCILE_ENABLED=False,
    )

    assert property_catalog.configured_property_catalog_schedules(disabled) == ()
    assert property_catalog.run_property_catalog_dev_reconcile(
        settings_object=disabled,
        workspace_id=WORKSPACE_A,
        mode=ReconcileMode.INCREMENTAL.value,
        runtime_factory_loader=Mock(side_effect=AssertionError("must not import")),
    ) == {
        "mode": "incremental",
        "status": "disabled",
        "workspace_id": WORKSPACE_A,
    }


def test_property_catalog_task_queue_accepts_only_workspace_isolation() -> None:
    workspace_queue = "property_catalog_dev_sidecar_22222222222242228222222222222222"

    assert configured_property_catalog_task_queue("") == (
        DEFAULT_PROPERTY_CATALOG_TASK_QUEUE
    )
    assert (
        configured_property_catalog_task_queue(DEFAULT_PROPERTY_CATALOG_TASK_QUEUE)
        == DEFAULT_PROPERTY_CATALOG_TASK_QUEUE
    )
    assert configured_property_catalog_task_queue(workspace_queue) == workspace_queue
    assert workspace_property_catalog_task_queue(WORKSPACE_A) == workspace_queue
    assert (
        configured_property_catalog_task_queue(
            None,
            reconcile_enabled=True,
            workspace_allowlist=(WORKSPACE_A,),
        )
        == workspace_queue
    )
    with pytest.raises(ValueError, match="does not match"):
        configured_property_catalog_task_queue(
            "property_catalog_dev_sidecar_33333333333343338333333333333333",
            reconcile_enabled=True,
            workspace_allowlist=(WORKSPACE_A,),
        )
    for unsafe in (
        "default",
        "property_catalog_dev_sidecar_other",
        "property_catalog_dev_sidecar_../../default",
        "property_catalog_dev_sidecar_22222222-2222-4222-8222-222222222222",
    ):
        with pytest.raises(ValueError):
            configured_property_catalog_task_queue(unsafe)


def test_enabled_schedule_is_bounded_and_skips_overlap() -> None:
    schedules = property_catalog.configured_property_catalog_schedules(_settings())

    assert len(schedules) == 1
    assert {item.activity_kwargs["workspace_id"] for item in schedules} == {
        WORKSPACE_A,
    }
    assert all(
        schedule.activity_name == property_catalog.PROPERTY_CATALOG_RECONCILE_ACTIVITY
        and schedule.interval_seconds
        == property_catalog.PROPERTY_CATALOG_RECONCILE_INTERVAL_SECONDS
        and schedule.overlap_policy is ScheduleOverlapPolicy.SKIP
        and schedule.queue == property_catalog.PROPERTY_CATALOG_TASK_QUEUE
        and schedule.activity_kwargs["mode"] == ReconcileMode.INCREMENTAL.value
        and "automatic persisted-state full repair" in schedule.description
        for schedule in schedules
    )


def test_enabled_schedule_accepts_oss_unset_cloud_in_exact_development() -> None:
    settings_object = _settings(PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT="")
    del settings_object.CLOUD_DEPLOYMENT

    configuration = property_catalog.property_catalog_schedule_configuration(
        settings_object
    )

    assert configuration.enabled is True
    assert len(configuration.requests) == 1
    assert configuration.requests[0].environment == "development"
    assert configuration.requests[0].cloud_deployment == ""


@pytest.mark.parametrize("environment", ("development", "staging"))
def test_enabled_schedule_preserves_existing_dev_cloud_behavior(
    environment: str,
) -> None:
    configuration = property_catalog.property_catalog_schedule_configuration(
        _settings(ENV_TYPE=environment)
    )

    assert configuration.enabled is True
    assert configuration.requests[0].cloud_deployment == "DEV"


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"ENV_TYPE": "production", "CLOUD_DEPLOYMENT": "DEV"}, "non-DEV"),
        (
            {
                "ENV_TYPE": "staging",
                "CLOUD_DEPLOYMENT": "",
                "PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT": "",
            },
            "only when ENV_TYPE=development",
        ),
        (
            {
                "CLOUD_DEPLOYMENT": "",
                "PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT": "DEV",
            },
            "differs",
        ),
        (
            {
                "CLOUD_DEPLOYMENT": "DEV",
                "PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT": "",
            },
            "differs",
        ),
        (
            {
                "CLOUD_DEPLOYMENT": "",
                "PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT": "",
                "PROPERTY_CATALOG_DEV_ENVIRONMENT": "staging",
            },
            "development-only",
        ),
        ({"ENV_TYPE": "development", "CLOUD_DEPLOYMENT": "US"}, "requires"),
        ({"PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST": ()}, "allowlist"),
        (
            {
                "PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST": (
                    WORKSPACE_A,
                    WORKSPACE_B,
                )
            },
            "workspace allowlist",
        ),
        ({"PROPERTY_CATALOG_DEV_MAX_WALL_MS": 100_001}, "wall"),
        (
            {"PROPERTY_CATALOG_DEV_SCHEDULED_RECONCILE_WALL_MS": 100_000},
            "scheduled reconcile wall",
        ),
        (
            {"PROPERTY_CATALOG_DEV_RUNTIME_FACTORY": "tests.catalog.runtime_factory"},
            "checked-in",
        ),
    ),
)
def test_activity_refuses_unsafe_scope_before_loading_runtime(
    overrides: dict[str, object],
    message: str,
) -> None:
    loader = Mock(side_effect=AssertionError("runtime/client boundary crossed"))

    with pytest.raises(property_catalog.PropertyCatalogScheduleError, match=message):
        property_catalog.run_property_catalog_dev_reconcile(
            settings_object=_settings(**overrides),
            workspace_id=WORKSPACE_A,
            mode=ReconcileMode.INCREMENTAL.value,
            runtime_factory_loader=loader,
        )

    loader.assert_not_called()


def test_activity_refuses_initial_backfill_wall_before_loading_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_request = property_catalog.configured_dev_rollout_request

    def extended_request(**kwargs: object) -> object:
        return replace(
            configured_request(**kwargs),  # type: ignore[arg-type]
            initial_backfill_wall_ms=DEV_STANDARD_MAX_WALL_MS + 1,
            scheduled_reconcile_wall_ms=None,
        )

    monkeypatch.setattr(
        property_catalog,
        "configured_dev_rollout_request",
        extended_request,
    )
    loader = Mock(side_effect=AssertionError("runtime/client boundary crossed"))

    with pytest.raises(
        property_catalog.PropertyCatalogScheduleError,
        match="refuses an initial backfill wall",
    ):
        property_catalog.run_property_catalog_dev_reconcile(
            settings_object=_settings(),
            workspace_id=WORKSPACE_A,
            mode=ReconcileMode.INCREMENTAL.value,
            runtime_factory_loader=loader,
        )

    loader.assert_not_called()


def test_activity_invokes_service_for_exact_allowlisted_workspace_and_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_object = _settings()
    runtime_factory = Mock(name="runtime_factory")
    loader = Mock(return_value=runtime_factory)
    runtime = Mock(name="runtime")
    runtime_factory.return_value = runtime
    scheduled = Mock(
        return_value={
            "mode": ReconcileMode.FULL_REPAIR,
            "workspace_id": WORKSPACE_A,
        }
    )
    monkeypatch.setattr(property_catalog, "run_workspace_reconcile", scheduled)
    monkeypatch.setattr(
        property_catalog,
        "require_checked_in_property_catalog_dev_runtime",
        lambda value: value,
    )

    result = property_catalog.run_property_catalog_dev_reconcile(
        settings_object=settings_object,
        workspace_id=WORKSPACE_A,
        mode=ReconcileMode.FULL_REPAIR.value,
        runtime_factory_loader=loader,
    )

    loader.assert_called_once_with(property_catalog.CHECKED_IN_DEV_RUNTIME_FACTORY_PATH)
    runtime_factory.assert_called_once()
    request = runtime_factory.call_args.args[0]
    assert request.workspace_id == WORKSPACE_A
    assert request.scheduled_reconcile_wall_ms == 1_200_000
    scheduled.assert_called_once_with(
        request=request,
        runtime=runtime,
        mode=ReconcileMode.FULL_REPAIR,
    )
    assert result == {
        "evidence": {
            "mode": ReconcileMode.FULL_REPAIR,
            "workspace_id": WORKSPACE_A,
        },
        "mode": ReconcileMode.FULL_REPAIR.value,
        "status": "completed",
        "workspace_id": WORKSPACE_A,
    }


def test_activity_rejects_factory_result_that_bypasses_checked_in_runtime() -> None:
    loader = Mock(return_value=lambda _request: object())

    with pytest.raises(
        property_catalog.PropertyCatalogScheduleError,
        match="reviewed checked-in DEV runtime",
    ):
        property_catalog.run_property_catalog_dev_reconcile(
            settings_object=_settings(),
            workspace_id=WORKSPACE_A,
            mode=ReconcileMode.INCREMENTAL.value,
            runtime_factory_loader=loader,
        )


def test_activity_rejects_unlisted_workspace_before_loading_runtime() -> None:
    loader = Mock(side_effect=AssertionError("runtime/client boundary crossed"))

    with pytest.raises(
        property_catalog.PropertyCatalogScheduleError,
        match="not allowlisted",
    ):
        property_catalog.run_property_catalog_dev_reconcile(
            settings_object=_settings(),
            workspace_id="44444444-4444-4444-8444-444444444444",
            mode=ReconcileMode.INCREMENTAL.value,
            runtime_factory_loader=loader,
        )

    loader.assert_not_called()


def test_activity_is_registered_without_temporal_retries() -> None:
    activity = property_catalog.reconcile_unified_property_catalog_dev

    assert (
        activity._activity_name == property_catalog.PROPERTY_CATALOG_RECONCILE_ACTIVITY
    )
    assert activity._metadata["max_retries"] == 0
    assert (
        activity._metadata["time_limit"]
        == property_catalog.PROPERTY_CATALOG_RECONCILE_ACTIVITY_TIME_LIMIT_SECONDS
    )
    assert activity._metadata["queue"] == property_catalog.PROPERTY_CATALOG_TASK_QUEUE
    assert property_catalog.PROPERTY_CATALOG_TASK_QUEUE != "default"
    assert "tfc.temporal.schedules.property_catalog" in TEMPORAL_ACTIVITY_MODULES


def test_activity_and_workflow_are_exclusive_to_dedicated_sidecar_queue() -> None:
    queue = property_catalog.PROPERTY_CATALOG_TASK_QUEUE

    def activity_names(queue_name: str) -> set[str]:
        return {
            value.__temporal_activity_definition.name
            for value in get_activities_for_queue(queue_name)
        }

    assert TaskRunnerWorkflow in get_workflows_for_queue(queue)
    assert property_catalog.PROPERTY_CATALOG_RECONCILE_ACTIVITY in activity_names(queue)
    for generic_queue in ("default", "tasks_s", "tasks_l", "tasks_xl"):
        assert (
            property_catalog.PROPERTY_CATALOG_RECONCILE_ACTIVITY
            not in activity_names(generic_queue)
        )
