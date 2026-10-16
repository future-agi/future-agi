"""Temporal activities for Insights reports.

Each stage is a thin wrapper around ``ee.agenthub.insights.pipeline``; the
database is the source of truth for progress, so a retried stage picks up
where the last attempt left off. Unexpected errors are logged and surface
as a generic message, never as internals.
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


async def _run(stage: str, fn, *args):
    from channels.db import database_sync_to_async

    from ee.agenthub.insights.llm import InsightModelError

    try:
        return await database_sync_to_async(fn, thread_sensitive=False)(*args)
    except InsightModelError as exc:
        raise ApplicationError(str(exc), non_retryable=not exc.unavailable) from exc
    except Exception as exc:
        logger.exception("insight_stage_failed", stage=stage)
        raise ApplicationError("The report could not be completed.") from exc


@activity.defn
async def insight_prepare(input: InsightRunInput) -> list[int]:
    from ee.agenthub.insights.pipeline import prepare

    return await _run("prepare", prepare, input.run_id)


@activity.defn
async def insight_extract(input: InsightBatchInput) -> dict:
    from ee.agenthub.insights.pipeline import extract_batch

    return await _run("extract", extract_batch, input.run_id, input.unit_pks)


@activity.defn
async def insight_retry_failed(input: InsightRunInput) -> list[int]:
    from ee.agenthub.insights.pipeline import retry_failed

    return await _run("retry", retry_failed, input.run_id)


@activity.defn
async def insight_organise(input: InsightRunInput) -> dict:
    from ee.agenthub.insights.pipeline import organise

    return await _run("organise", organise, input.run_id)


@activity.defn
async def insight_finish(input: InsightRunInput) -> dict:
    from ee.agenthub.insights.pipeline import finish

    return await _run("finish", finish, input.run_id)


@activity.defn
async def insight_fail(input: InsightFailInput) -> None:
    from ee.agenthub.insights.pipeline import fail

    await _run("fail", fail, input.run_id, input.message)
