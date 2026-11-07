"""The production registries load Skills on the existing native-worker queue."""

from temporalio import activity


def test_native_workflows_and_activities_registered_on_tasks_l():
    from tfc.temporal.common import registry
    from tfc.temporal.skills_hub.workflows import SkillsJobWorkflow

    assert SkillsJobWorkflow in registry.get_workflows_for_queue("tasks_l")
    assert SkillsJobWorkflow not in registry.get_workflows_for_queue("default")
    names = {
        definition.name
        for function in registry.get_activities_for_queue("tasks_l")
        if (definition := activity._Definition.from_callable(function)) is not None
    }
    assert {
        "skills_start_job",
        "skills_execute_attempt",
        "skills_execute_once",
        "skills_finish_job",
        "reconcile_skills_jobs",
        "purge_skills_payloads",
    } <= names
    assert "model_hub.tasks.skills_hub_v11" in registry.TEMPORAL_ACTIVITY_MODULES


def test_maintenance_schedules_and_standalone_polling():
    from tfc.management.commands.start_temporal_worker import _generic_all_queues
    from tfc.temporal.common.registry import get_all_queues
    from tfc.temporal.schedules.model_hub import MODEL_HUB_SCHEDULES

    # embedded._worker_plans uses this same queue selection. The standalone
    # supervisor enables that worker in the API process, not a new container.
    assert "tasks_l" in _generic_all_queues(get_all_queues())
    schedules = {schedule.activity_name: schedule for schedule in MODEL_HUB_SCHEDULES}
    assert schedules["reconcile_skills_jobs"].queue == "tasks_l"
    assert schedules["reconcile_skills_jobs"].interval_seconds == 30
    assert schedules["purge_skills_payloads"].queue == "tasks_l"
    assert schedules["purge_skills_payloads"].interval_seconds == 86400
