"""Workflow history carries IDs and bounded control metadata, never private text."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class SkillsJobWorkflow:
    @workflow.run
    async def run(self, request: dict) -> dict:
        control = {
            key: request[key] for key in ("job_id", "organization_id", "workspace_id")
        }
        failure = None
        try:
            state = await workflow.execute_activity(
                "skills_start_job",
                control,
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            if state["status"] not in {"queued", "running"}:
                return {"id": state["id"], "status": state["status"]}
            deadline = workflow.now() + timedelta(seconds=state["remaining_seconds"])
            if state["kind"] == "test":
                ids = state["attempts"]
                # A replay reconstructs completed activity handles, not new provider calls.
                concurrency = state["max_active_attempts"]
                for start in range(0, len(ids), concurrency):
                    remaining = (deadline - workflow.now()).total_seconds()
                    if remaining <= 0:
                        failure = "provider_timeout"
                        break
                    results = await asyncio.gather(
                        *(
                            workflow.execute_activity(
                                "skills_execute_attempt",
                                {**control, "attempt_id": attempt_id},
                                start_to_close_timeout=timedelta(seconds=150),
                                heartbeat_timeout=timedelta(seconds=20),
                                schedule_to_start_timeout=timedelta(
                                    seconds=min(60, remaining)
                                ),
                                retry_policy=RetryPolicy(maximum_attempts=1),
                            )
                            for attempt_id in ids[start : start + concurrency]
                        )
                    )
                    failure = next(
                        (result["code"] for result in results if result.get("code")),
                        None,
                    )
                    if failure:
                        break
            else:
                await workflow.execute_activity(
                    "skills_execute_once",
                    {**control, "kind": state["kind"]},
                    start_to_close_timeout=timedelta(seconds=75),
                    heartbeat_timeout=timedelta(seconds=20),
                    schedule_to_start_timeout=timedelta(
                        seconds=max(1, min(60, state["remaining_seconds"]))
                    ),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
        except ActivityError:
            failure = "execution_unknown"
        except asyncio.CancelledError:
            failure = "execution_unknown"
        return await workflow.execute_activity(
            "skills_finish_job",
            {**control, "failure_code": failure},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
