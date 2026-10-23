"""Temporal activities for Insights reports.

Each stage is a thin wrapper around ``ee.agenthub.insights.pipeline``; the
database is the source of truth for progress, so a retried stage picks up
where the last attempt left off. Unexpected errors are logged and surface
as a generic message, never as internals.

Insights is Enterprise-only. Its views check the license on every request,
and each stage checks it again for the run's organization, so a run on an
install without it (the license lapsed, or the run predates the gate) fails
with a clear error instead of reading.
"""

import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tfc.temporal.insights.types import (
    InsightBatchInput,
    InsightFailInput,
    InsightRunInput,
)

logger = structlog.get_logger(__name__)


def require_license(run_id: str) -> None:
    """Refuse a stage of a run whose organization may not use Insights, or
    whose Turing models it may not call, with a non-retryable error."""
    from tfc.ee_gates import (
        INSIGHTS_DENIED_MESSAGE,
        INSIGHTS_TURING_DENIED_MESSAGE,
        insights_turing_denied,
    )
    from tfc.ee_gating import EEFeature, FeatureUnavailable, check_ee_feature
    from tracer.models.insights import InsightRun

    # Any workspace context: the run's own organization decides.
    org_id = (
        InsightRun.no_workspace_objects.filter(id=run_id)
        .values_list("organization_id", flat=True)
        .first()
    )
    if org_id is None:
        return  # The stage reports the missing run itself.
    try:
        check_ee_feature(EEFeature.INSIGHTS, org_id=str(org_id))
    except FeatureUnavailable:
        raise ApplicationError(
            INSIGHTS_DENIED_MESSAGE, type="CapabilityDenied", non_retryable=True
        ) from None
    if insights_turing_denied(org_id):
        raise ApplicationError(
            INSIGHTS_TURING_DENIED_MESSAGE,
            type="CapabilityDenied",
            non_retryable=True,
        )


def _licensed(fn):
    def call(run_id, *args):
        require_license(run_id)
        return fn(run_id, *args)

    return call


NOT_IN_BUILD = "Insights isn't available in this build."


def _pipeline(name: str):
    """A stage of ``ee.agenthub.insights.pipeline``. A build without it (the
    open tree) fails the stage for good instead of retrying an import."""
    try:
        from ee.agenthub.insights import pipeline
    except ImportError:
        raise ApplicationError(
            NOT_IN_BUILD, type="CapabilityDenied", non_retryable=True
        ) from None
    return getattr(pipeline, name)


def _model_error_class():
    try:
        from ee.agenthub.insights.llm import InsightModelError
    except ImportError:
        return None
    return InsightModelError


async def _run(stage: str, fn, *args, licensed: bool = True):
    from channels.db import database_sync_to_async

    model_error = _model_error_class()
    try:
        return await database_sync_to_async(
            _licensed(fn) if licensed else fn, thread_sensitive=False
        )(*args)
    except ApplicationError:
        raise
    except Exception as exc:
        if model_error is not None and isinstance(exc, model_error):
            raise ApplicationError(str(exc), non_retryable=not exc.unavailable) from exc
        logger.exception("insight_stage_failed", stage=stage)
        raise ApplicationError("The report could not be completed.") from exc


@activity.defn
async def insight_prepare(input: InsightRunInput) -> list[int]:
    prepare = _pipeline("prepare")

    return await _run("prepare", prepare, input.run_id)


@activity.defn
async def insight_extract(input: InsightBatchInput) -> dict:
    extract_batch = _pipeline("extract_batch")

    return await _run("extract", extract_batch, input.run_id, input.unit_pks)


@activity.defn
async def insight_retry_failed(input: InsightRunInput) -> list[int]:
    retry_failed = _pipeline("retry_failed")

    return await _run("retry", retry_failed, input.run_id)


@activity.defn
async def insight_organise(input: InsightRunInput) -> dict:
    organise = _pipeline("organise")

    return await _run("organise", organise, input.run_id)


@activity.defn
async def insight_finish(input: InsightRunInput) -> dict:
    finish = _pipeline("finish")

    return await _run("finish", finish, input.run_id)


@activity.defn
async def insight_fail(input: InsightFailInput) -> None:
    fail = _pipeline("fail")

    # Recording the failure needs no license: it is how a refused run ends.
    await _run("fail", fail, input.run_id, input.message, licensed=False)
