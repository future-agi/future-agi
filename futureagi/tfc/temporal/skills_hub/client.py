"""Non-destructive dispatch; unknown acceptance always reuses the same identity."""

import asyncio
from datetime import datetime, timedelta
from uuid import UUID

from django.utils import timezone
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from model_hub.services.skills_hub.v11.types import SkillScope
from tfc.temporal.common.client import _run_async_in_sync_context, start_workflow_async
from tfc.temporal.skills_hub.workflows import SkillsJobWorkflow


def start_skills_job(*, job_id: UUID, scope: SkillScope, deadline_at: datetime) -> None:
    async def dispatch() -> None:
        await asyncio.wait_for(
            start_workflow_async(
                SkillsJobWorkflow,
                {
                    "job_id": str(job_id),
                    "organization_id": str(scope.organization_id),
                    "workspace_id": str(scope.workspace_id),
                },
                workflow_id=f"skills-job-{job_id}",
                task_queue="tasks_l",
                cancel_existing=False,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                execution_timeout=timedelta(
                    seconds=max(1, (deadline_at - timezone.now()).total_seconds()) + 180
                ),
            ),
            timeout=5,
        )

    try:
        # Same persistent, context-preserving bridge used by the existing drop-in dispatcher.
        _run_async_in_sync_context(dispatch)
    except WorkflowAlreadyStartedError:
        # A rejected replay is not permission to terminate or create a replacement workflow.
        return
    except RPCError as error:
        raise ConnectionError(
            "Skills workflow acceptance could not be confirmed."
        ) from error
