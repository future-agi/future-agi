"""Short scheduled maintenance only. Paid execution uses SkillsJobWorkflow."""

from tfc.temporal import temporal_activity


@temporal_activity(
    name="reconcile_skills_jobs", queue="tasks_l", time_limit=60, max_retries=0
)
def reconcile_skills_jobs() -> dict:
    from model_hub.services.skills_hub.v11.jobs import reconcile_jobs

    return reconcile_jobs(batch_size=100)


@temporal_activity(
    name="purge_skills_payloads", queue="tasks_l", time_limit=120, max_retries=0
)
def purge_skills_payloads() -> dict:
    from model_hub.services.skills_hub.v11.jobs import purge_expired_payloads
    from model_hub.services.skills_hub.v12.capture import purge_expired_captures
    from model_hub.services.skills_hub.v12.consumers import (
        purge_expired_consumer_metadata,
    )

    return {
        **purge_expired_payloads(batch_size=100),
        "context_capture_payloads": purge_expired_captures(batch_size=100),
        "context_consumer_metadata": purge_expired_consumer_metadata(batch_size=100),
    }
