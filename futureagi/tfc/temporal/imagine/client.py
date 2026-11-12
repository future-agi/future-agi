"""Client for starting Imagine analysis workflows."""

import structlog

from tfc.temporal.common.client import start_workflow_sync
from tfc.temporal.imagine.types import ImagineAnalysisInput
from tfc.temporal.imagine.workflows import ImagineAnalysisWorkflow

logger = structlog.get_logger(__name__)


def start_imagine_analysis(
    analysis_id: str,
    trace_id: str,
    org_id: str,
    prompt: str,
    task_queue: str = "tasks_xl",
    *,
    project_id: str | None = None,
) -> str:
    """Start an analysis workflow. Returns workflow ID.

    Uses the common client's start_workflow_sync which handles
    ASGI event loop detection and thread pool execution.
    """
    if not project_id or not org_id:
        raise ValueError("Imagine project binding missing.")

    # Only new, bound executions receive this provenance version. Existing
    # workflow histories keep their original IDs and activity commands.
    workflow_id = f"imagine-pb1-{analysis_id}"

    start_workflow_sync(
        ImagineAnalysisWorkflow,
        ImagineAnalysisInput(
            analysis_id=analysis_id,
            trace_id=trace_id,
            org_id=org_id,
            prompt=prompt,
            project_id=project_id,
        ),
        workflow_id=workflow_id,
        task_queue=task_queue,
        cancel_existing=False,
    )

    return workflow_id
