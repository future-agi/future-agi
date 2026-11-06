"""The sweep that ends a fix nothing drives any more."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from simulate.models import AgentFixRun
from simulate.services.agent_fix_sweep import START_GRACE, sweep_lost_fix_runs
from simulate.tests.test_agent_fix_services import _fix_run
from simulate.tests.test_agent_fix_services import (  # noqa: F401 - fixtures
    environment as environment,
)
from simulate.tests.test_agent_fix_services import source_run as source_run
from simulate.tests.test_agent_fix_services import temporal as temporal
from simulate.tests.test_agent_fix_services import versioning_on as versioning_on
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

State = AgentFixRun.State
SWEEP = "simulate.services.agent_fix_sweep"
# _fix_run allows 30 minutes; the token grace is 10 more.
OVERDUE = timedelta(minutes=30 + 10 + 1)


@pytest.fixture
def workflows():
    """The fixes whose workflow still runs, and the sandboxes the sweep released."""
    running: set[str] = set()
    with (
        patch(
            f"{SWEEP}.agent_fix_workflow_running",
            side_effect=lambda fix_run_id: fix_run_id in running,
        ) as asked,
        patch(f"{SWEEP}.release") as release,
    ):
        yield SimpleNamespace(running=running, asked=asked, release=release)


def _aged(fix_run, age=START_GRACE + timedelta(minutes=1)):
    """The fix as if it was created, and started when it ran, ``age`` ago."""
    then = timezone.now() - age
    AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
        created_at=then, started_at=then if fix_run.started_at else None
    )
    fix_run.refresh_from_db()
    return fix_run


def test_a_fix_its_workflow_still_drives_is_left_alone(source_run, workflows):
    fix_run = _aged(_fix_run(source_run))
    workflows.running.add(str(fix_run.id))

    assert sweep_lost_fix_runs() == {"checked": 1, "closed": 0}

    fix_run.refresh_from_db()
    assert fix_run.state == State.RUNNING
    workflows.release.assert_not_called()


@pytest.mark.parametrize(
    ("state", "started", "ended", "code"),
    [
        (State.RUNNING, True, State.FAILED, "fix_workflow_lost"),
        (State.QUEUED, False, State.FAILED, "fix_dispatch_failed"),
        (State.STOPPING, True, State.STOPPED, None),
    ],
    ids=["running", "never-started", "stopping"],
)
def test_a_fix_whose_workflow_is_gone_ends_and_frees_its_sandbox(
    source_run, workflows, state, started, ended, code
):
    fix_run = _fix_run(source_run, state=state)
    if not started:
        AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(started_at=None)
        fix_run.refresh_from_db()
    fix_run = _aged(fix_run)

    assert sweep_lost_fix_runs() == {"checked": 1, "closed": 1}

    fix_run.refresh_from_db()
    assert fix_run.state == ended
    assert (fix_run.failure or {}).get("code") == code
    assert fix_run.finished_at is not None
    assert fix_run.token_hash == ""
    workflows.release.assert_called_once_with(str(fix_run.id))


def test_a_fix_past_its_deadline_ends_even_while_its_workflow_runs(
    source_run, workflows
):
    fix_run = _aged(_fix_run(source_run), OVERDUE)
    workflows.running.add(str(fix_run.id))

    assert sweep_lost_fix_runs() == {"checked": 1, "closed": 1}

    fix_run.refresh_from_db()
    assert (fix_run.state, fix_run.failure["code"]) == (State.FAILED, "fix_deadline")
    # A stuck workflow can still describe itself as running; it is not asked.
    workflows.asked.assert_not_called()


def test_a_new_fix_is_given_time_to_start(source_run, workflows):
    _fix_run(source_run, state=State.QUEUED)

    assert sweep_lost_fix_runs() == {"checked": 0, "closed": 0}

    workflows.asked.assert_not_called()


def test_a_fix_the_sweep_cannot_check_is_left_for_the_next_pass(source_run, workflows):
    fix_run = _aged(_fix_run(source_run))
    workflows.asked.side_effect = RuntimeError("temporal unavailable")

    assert sweep_lost_fix_runs() == {"checked": 1, "closed": 0}

    fix_run.refresh_from_db()
    assert fix_run.state == State.RUNNING


def test_the_sweep_runs_every_five_minutes_as_a_registered_activity():
    from simulate.tasks.agent_fix import sweep_lost_agent_fixes
    from tfc.temporal.common import registry
    from tfc.temporal.drop_in.decorator import _ACTIVITY_WRAPPERS
    from tfc.temporal.schedules import ALL_SCHEDULES

    (config,) = [
        schedule
        for schedule in ALL_SCHEDULES
        if schedule.schedule_id == "sweep-lost-agent-fixes"
    ]
    assert (config.activity_name, config.interval_seconds, config.queue) == (
        sweep_lost_agent_fixes._activity_name,
        300,
        "default",
    )
    assert sweep_lost_agent_fixes._metadata["max_retries"] == 0
    activities = registry.get_activities_for_queue("default")
    assert _ACTIVITY_WRAPPERS["sweep_lost_agent_fixes"] in activities
