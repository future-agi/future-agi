"""The periodic recovery runs on the queue that registers its activity."""

import pytest

pytestmark = pytest.mark.unit


def test_feed_sweep_is_registered_on_five_minute_schedule_and_tasks_queue():
    from tfc.temporal.common import registry
    from tfc.temporal.drop_in.decorator import _ACTIVITY_WRAPPERS, get_activity_by_name
    from tfc.temporal.drop_in.workflow import TaskRunnerWorkflow
    from tfc.temporal.schedules.tracer import TRACER_SCHEDULES
    from tracer.services.feed_escalation import sweep_feed_escalations

    schedule = next(
        row for row in TRACER_SCHEDULES if row.schedule_id == "sweep-feed-escalations"
    )
    assert schedule.interval_seconds == 300
    assert schedule.queue == "tasks_s"
    assert schedule.activity_name == "sweep_feed_escalations"
    activities = registry.get_activities_for_queue(schedule.queue)
    wrapper = _ACTIVITY_WRAPPERS[sweep_feed_escalations.name]
    assert wrapper in activities
    assert TaskRunnerWorkflow in registry.get_workflows_for_queue(schedule.queue)
    assert get_activity_by_name(sweep_feed_escalations.name)["queue"] == schedule.queue
    assert wrapper not in registry.get_activities_for_queue("exact_aggregation")
