"""
Temporal workflows for ground truth embedding generation.

IMPORTANT: Do NOT use workflow.logger — it uses Python's stdlib logging
which acquires locks and causes deadlocks. Logging is done in activities.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from tfc.temporal.ground_truth.types import (
    GenerateEmbeddingsInput,
    GenerateEmbeddingsWorkflowInput,
    GenerateEmbeddingsWorkflowOutput,
)

EMBEDDING_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=2,
    backoff_coefficient=2.0,
)


@workflow.defn
class GenerateGroundTruthEmbeddingsWorkflow:
    """
    Workflow to generate embeddings for a ground truth dataset.

    Triggered after upload or role-mapping change.
    Runs on tasks_xl queue since embedding generation is long-running.
    """

    @workflow.run
    async def run(
        self, input: GenerateEmbeddingsWorkflowInput
    ) -> GenerateEmbeddingsWorkflowOutput:
        result = await workflow.execute_activity(
            "generate_ground_truth_embeddings_activity",
            GenerateEmbeddingsInput(ground_truth_id=input.ground_truth_id),
            task_queue="tasks_xl",
            start_to_close_timeout=timedelta(minutes=30),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=EMBEDDING_RETRY_POLICY,
        )

        return GenerateEmbeddingsWorkflowOutput(
            ground_truth_id=result.get("ground_truth_id", input.ground_truth_id),
            rows_embedded=result.get("rows_embedded", 0),
            status=result.get("status", "failed"),
            error=result.get("error"),
        )
