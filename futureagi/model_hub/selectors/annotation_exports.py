"""Resolve exports whose worker exited without writing a terminal job state."""

from model_hub.models.annotation_queues import AnnotationExportJob
from model_hub.models.choices import AnnotationExportJobStatus
from tfc.temporal.common.client import get_workflow_status_sync


def resolve_export_job_state(job: AnnotationExportJob) -> tuple[str, str | None]:
    """Read Temporal only for in-flight jobs; an outage is not a failed export."""
    if job.status not in (
        AnnotationExportJobStatus.PENDING.value,
        AnnotationExportJobStatus.RUNNING.value,
    ):
        return job.status, job.error
    if job.queue_id is None:
        return (
            AnnotationExportJobStatus.FAILED.value,
            "The annotation queue was deleted before export completed.",
        )
    if not job.workflow_id:
        return job.status, job.error

    workflow = get_workflow_status_sync(job.workflow_id, timeout_seconds=2)
    if workflow is None:
        return job.status, job.error
    reason = {
        "FAILED": "failed before saving its result",
        "TIMED_OUT": "timed out",
        "CANCELED": "was cancelled",
        "TERMINATED": "was stopped",
    }.get(workflow["status_name"])
    if reason is None:
        return job.status, job.error
    return (
        AnnotationExportJobStatus.FAILED.value,
        f"The background export {reason}. Please retry.",
    )
