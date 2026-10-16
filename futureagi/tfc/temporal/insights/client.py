"""Start Insights report workflows."""

from tfc.temporal.common.client import start_workflow_sync
from tfc.temporal.insights.types import InsightRunInput
from tfc.temporal.insights.workflows import InsightReportWorkflow


def start_insight_run(run_id: str, task_queue: str = "tasks_xl") -> str:
    workflow_id = f"insight-run-{run_id}"
    start_workflow_sync(
        InsightReportWorkflow,
        InsightRunInput(run_id=str(run_id)),
        workflow_id=workflow_id,
        task_queue=task_queue,
        cancel_existing=False,
    )
    return workflow_id
