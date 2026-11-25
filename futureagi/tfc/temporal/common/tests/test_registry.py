from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from tfc import ee_loader
from tfc.temporal.common import registry


def test_hosted_runner_dropin_stays_on_its_dedicated_queue():
    from simulate.tasks.hosted_harness_conversation import (
        ensure_hosted_harness_conversation_runtime,
    )
    from simulate.temporal.constants import QUEUE_RUNNER
    from tfc.temporal.drop_in.decorator import _ACTIVITY_WRAPPERS
    from tfc.temporal.drop_in.workflow import TaskRunnerWorkflow

    runner_activities = registry.get_activities_for_queue(QUEUE_RUNNER)
    runner = _ACTIVITY_WRAPPERS[ensure_hosted_harness_conversation_runtime.name]
    assert runner in runner_activities
    assert TaskRunnerWorkflow in registry.get_workflows_for_queue(QUEUE_RUNNER)
    for queue in (
        "default",
        "tasks_s",
        "tasks_l",
        "tasks_xl",
        "agent_compass",
        "trace_ingestion",
        "exact_aggregation",
    ):
        assert runner not in registry.get_activities_for_queue(queue)


def test_agent_version_preflight_runs_on_the_runner_queue():
    from simulate.tasks.harness_agent_versions import preflight_harness_agent_version
    from simulate.temporal.constants import QUEUE_RUNNER
    from tfc.temporal.drop_in.decorator import _ACTIVITY_WRAPPERS

    runner_activities = registry.get_activities_for_queue(QUEUE_RUNNER)
    runner = _ACTIVITY_WRAPPERS[preflight_harness_agent_version.name]
    assert runner in runner_activities
    for queue in ("default", "tasks_s", "tasks_l", "tasks_xl"):
        assert runner not in registry.get_activities_for_queue(queue)


def test_agent_change_detection_runs_on_the_default_queue():
    from simulate.tasks.harness_agent_changes import (
        observe_harness_github_push,
        poll_harness_github_heads,
    )
    from simulate.temporal.constants import QUEUE_RUNNER
    from tfc.temporal.drop_in.decorator import _ACTIVITY_WRAPPERS

    default_activities = registry.get_activities_for_queue("default")
    runner_activities = registry.get_activities_for_queue(QUEUE_RUNNER)
    for task in (poll_harness_github_heads, observe_harness_github_push):
        wrapper = _ACTIVITY_WRAPPERS[task.name]
        assert wrapper in default_activities
        assert wrapper not in runner_activities


def test_nudge_activities_run_on_the_default_queue():
    from simulate.tasks.harness_nudges import (
        check_harness_nudges,
        finish_harness_nudge_turn,
        raise_harness_environment_nudge,
        reconcile_harness_environment_nudges,
        retry_harness_deferred_activations,
        stop_harness_nudge_sandbox,
        tick_harness_nudges,
    )
    from simulate.temporal.constants import QUEUE_RUNNER
    from tfc.temporal.drop_in.decorator import _ACTIVITY_WRAPPERS
    from tfc.temporal.schedules.simulate import SIMULATE_SCHEDULES

    default_activities = registry.get_activities_for_queue("default")
    runner_activities = registry.get_activities_for_queue(QUEUE_RUNNER)
    for task in (
        raise_harness_environment_nudge,
        reconcile_harness_environment_nudges,
        tick_harness_nudges,
        check_harness_nudges,
        finish_harness_nudge_turn,
        stop_harness_nudge_sandbox,
    ):
        wrapper = _ACTIVITY_WRAPPERS[task.name]
        assert wrapper in default_activities
        assert wrapper not in runner_activities
    # Activations run where the agent check runs.
    retry = _ACTIVITY_WRAPPERS[retry_harness_deferred_activations.name]
    assert retry in runner_activities
    [tick] = [
        one for one in SIMULATE_SCHEDULES if one.schedule_id == "tick-harness-nudges"
    ]
    assert (tick.activity_name, tick.interval_seconds, tick.queue) == (
        "tick_harness_nudges",
        30,
        "default",
    )


def test_the_removed_authoring_activity_is_not_registered():
    from simulate.temporal.constants import QUEUE_RUNNER

    names = {
        definition.name
        for activity in registry.get_activities_for_queue(QUEUE_RUNNER)
        if (definition := getattr(activity, "__temporal_activity_definition", None))
    }
    assert "launch_hosted_harness_job" in names
    assert "author_hosted_harness_job" not in names


def test_usage_temporal_registry_prefers_cloud(monkeypatch):
    cloud_registry = MagicMock()
    import_module = MagicMock(
        return_value=SimpleNamespace(get_workflows=cloud_registry)
    )
    monkeypatch.setattr(registry, "import_module", import_module)

    assert registry._load_usage_temporal_registry("get_workflows") is cloud_registry
    import_module.assert_called_once_with("ee.cloud.temporal")


def test_usage_temporal_registry_falls_back_to_legacy(monkeypatch):
    legacy_registry = MagicMock()
    cloud_missing = ModuleNotFoundError(
        "cloud module unavailable", name="ee.cloud.temporal"
    )
    import_module = MagicMock(
        side_effect=[
            cloud_missing,
            SimpleNamespace(get_activities=legacy_registry),
        ],
    )
    monkeypatch.setattr(registry, "import_module", import_module)

    assert registry._load_usage_temporal_registry("get_activities") is legacy_registry
    assert import_module.call_args_list == [
        call("ee.cloud.temporal"),
        call("ee.usage.temporal"),
    ]


def test_usage_temporal_registry_returns_none_when_unavailable(monkeypatch):
    missing_modules = [
        ModuleNotFoundError("cloud unavailable", name="ee.cloud"),
        ModuleNotFoundError("legacy unavailable", name="ee.usage.temporal"),
    ]
    import_module = MagicMock(side_effect=missing_modules)
    monkeypatch.setattr(registry, "import_module", import_module)

    assert registry._load_usage_temporal_registry("get_workflows") is None
    assert import_module.call_args_list == [
        call("ee.cloud.temporal"),
        call("ee.usage.temporal"),
    ]


def test_usage_events_are_on_where_the_registry_finds_their_consumer(monkeypatch):
    # One tuple decides both: whether settings turn usage events on, and
    # where the worker loads the workflow that drains them.
    import_module = MagicMock(
        side_effect=[
            ModuleNotFoundError("missing", name=name)
            for name in ee_loader.USAGE_TEMPORAL_MODULES
        ]
    )
    monkeypatch.setattr(registry, "import_module", import_module)

    registry._load_usage_temporal_registry("get_workflows")

    assert [c.args[0] for c in import_module.call_args_list] == list(
        ee_loader.USAGE_TEMPORAL_MODULES
    )
    for name in ee_loader.USAGE_TEMPORAL_MODULES:
        monkeypatch.setattr(
            ee_loader, "has_ee", lambda module, name=name: module == name
        )
        assert ee_loader.usage_event_consumer_available()
    monkeypatch.setattr(ee_loader, "has_ee", lambda module: False)
    assert not ee_loader.usage_event_consumer_available()


def test_usage_temporal_registry_does_not_hide_internal_import_error(monkeypatch):
    import_error = ModuleNotFoundError("dependency unavailable", name="redis")
    monkeypatch.setattr(registry, "import_module", MagicMock(side_effect=import_error))

    try:
        registry._load_usage_temporal_registry("get_workflows")
    except ModuleNotFoundError as exc:
        assert exc is import_error
    else:  # pragma: no cover - assertion guard
        raise AssertionError("internal import failure was silently ignored")


@pytest.mark.parametrize(
    ("register", "args"),
    [
        (registry._register_usage_temporal_workflows, ()),
        (registry._register_usage_temporal_activities, (MagicMock(),)),
    ],
)
def test_usage_temporal_registration_does_not_hide_packaging_failure(
    monkeypatch, register, args
):
    import_error = ModuleNotFoundError("dependency unavailable", name="redis")
    monkeypatch.setattr(
        registry,
        "_load_usage_temporal_registry",
        MagicMock(side_effect=import_error),
    )

    with pytest.raises(ModuleNotFoundError) as exc_info:
        register(*args)

    assert exc_info.value is import_error


def test_scoring_sweeper_schedule_registered():
    """The sweeper runs every minute on the small-task queue, as a registered
    activity with its own time limit."""
    from simulate.services.scoring_status import SCORING_SWEEP_TIME_LIMIT_SECONDS
    from simulate.tasks.chat_sim import sweep_stuck_scoring
    from tfc.temporal.drop_in.decorator import _ACTIVITY_WRAPPERS
    from tfc.temporal.schedules import ALL_SCHEDULES
    from tfc.temporal.schedules.manager import _build_schedule_for_config

    (config,) = [
        schedule
        for schedule in ALL_SCHEDULES
        if schedule.schedule_id == "sweep-stuck-scoring"
    ]
    assert config.activity_name == "sweep_stuck_scoring"
    assert config.activity_name == sweep_stuck_scoring._activity_name
    assert config.interval_seconds == 60
    assert config.queue == "tasks_s"
    assert sweep_stuck_scoring._metadata["queue"] == "tasks_s"
    assert sweep_stuck_scoring._metadata["time_limit"] == (
        SCORING_SWEEP_TIME_LIMIT_SECONDS
    )
    run_input = _build_schedule_for_config(config).action.args[0]
    assert run_input.time_limit == SCORING_SWEEP_TIME_LIMIT_SECONDS == 300
    # One attempt: a pass that outlives its limit is not started again beside
    # its own still-running thread.
    assert sweep_stuck_scoring._metadata["max_retries"] == 0
    assert run_input.max_retries == 0
    assert "simulate.tasks.chat_sim" in registry.TEMPORAL_ACTIVITY_MODULES
    activities = registry.get_activities_for_queue("tasks_s")
    assert _ACTIVITY_WRAPPERS["sweep_stuck_scoring"] in activities
