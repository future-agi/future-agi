"""Temporal activity wrapper for GroundTruthService.embed_dataset."""

import structlog
from django.db import close_old_connections
from temporalio import activity

from model_hub.models.evals_metric import EvalGroundTruth
from tfc.temporal.ground_truth.types import (
    GenerateEmbeddingsInput,
    GenerateEmbeddingsOutput,
)


logger = structlog.get_logger(__name__)


def _run_embed_dataset(ground_truth_id: str) -> GenerateEmbeddingsOutput:
    """Synchronous path executed by the Temporal activity."""
    close_old_connections()

    from model_hub.services.ground_truth_service import GroundTruthService

    try:
        gt = EvalGroundTruth.objects.get(id=ground_truth_id, deleted=False)
    except EvalGroundTruth.DoesNotExist:
        return GenerateEmbeddingsOutput(
            ground_truth_id=ground_truth_id,
            rows_embedded=0,
            status=EvalGroundTruth.EmbeddingStatus.FAILED,
            error="Ground truth not found",
        )

    def _heartbeat(rows_done: int) -> None:
        try:
            activity.heartbeat(rows_done)
        except Exception:
            pass

    result = GroundTruthService.embed_dataset(gt=gt, heartbeat=_heartbeat)
    return GenerateEmbeddingsOutput(
        ground_truth_id=result.ground_truth_id,
        rows_embedded=result.rows_embedded,
        status=result.status,
        error=result.error,
    )


@activity.defn
async def generate_ground_truth_embeddings_activity(
    input: GenerateEmbeddingsInput,
) -> GenerateEmbeddingsOutput:
    """Temporal entry point for the embed-dataset workflow.

    Runs on the ``tasks_xl`` queue because the underlying CH bulk write
    fans out to a 20-thread pool and can saturate a small worker pod.
    """
    logger.info(
        "generate_ground_truth_embeddings_start",
        gt_id=input.ground_truth_id,
    )

    from tfc.telemetry import otel_sync_to_async

    try:
        result = await otel_sync_to_async(_run_embed_dataset)(
            input.ground_truth_id
        )
    except Exception as exc:
        logger.exception(
            "generate_ground_truth_embeddings_error",
            gt_id=input.ground_truth_id,
            error=str(exc),
        )
        _force_mark_failed(input.ground_truth_id, str(exc))
        return GenerateEmbeddingsOutput(
            ground_truth_id=input.ground_truth_id,
            rows_embedded=0,
            status=EvalGroundTruth.EmbeddingStatus.FAILED,
            error=str(exc),
        )

    logger.info(
        "generate_ground_truth_embeddings_done",
        gt_id=input.ground_truth_id,
        rows_embedded=result.rows_embedded,
        status=result.status,
    )
    return result


def _force_mark_failed(ground_truth_id: str, reason: str) -> None:
    """Last-resort PG status update when the service raises before it can
    mark itself failed (e.g. DB connection drops mid-embed). Best-effort:
    we already logged the original exception, so a second failure here
    is swallowed rather than masking the first."""
    try:
        close_old_connections()

        gt = EvalGroundTruth.objects.get(id=ground_truth_id)
        gt.embedding_status = EvalGroundTruth.EmbeddingStatus.FAILED
        gt.save(update_fields=["embedding_status", "updated_at"])
    except Exception:
        logger.warning(
            "ground_truth_force_mark_failed_no_op",
            gt_id=ground_truth_id,
            reason=reason,
        )
