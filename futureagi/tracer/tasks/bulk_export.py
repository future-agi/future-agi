from tfc.temporal import temporal_activity
from tracer.services.bulk_export import JOB_TIMEOUT_SECONDS, run_export


@temporal_activity(max_retries=0, time_limit=JOB_TIMEOUT_SECONDS, queue="tasks_l")
def process_bulk_export(job_id):
    """Stream one durable export job; duplicate deliveries are harmless."""
    run_export(job_id)
