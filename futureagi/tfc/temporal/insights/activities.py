"""Temporal activities for Insights reports.

Each stage is a thin wrapper around ``ee.agenthub.insights.pipeline``; the
database is the source of truth for progress, so a retried stage picks up
where the last attempt left off. Unexpected errors are logged and surface
as a generic message, never as internals.

Insights is Enterprise-only. Its views check the license on every request,
and each stage checks it again for the run's organization, so a run on an
install without it (the license lapsed, or the run predates the gate) fails
with a clear error instead of reading. An entitlement service that can't
answer is retried, not taken for a refusal.
"""

import asyncio

import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.insights.types import (
    InsightBatchInput,
    InsightFailInput,
    InsightRunInput,
)

logger = structlog.get_logger(__name__)


# The run's error code for a refusal (ee.agenthub.insights.llm.LICENSE): the
# report page offers the License settings, not a Retry that would be refused.
LICENSE_CODE = "license"


def require_license(run_id: str) -> None:
    """Refuse a stage whose organization may not use Insights or its Turing
    models, for good and with LICENSE_CODE. Retry entitlement outages rather
    than treating them as denials."""
    from tfc.capabilities.service import refusal_message
    from tfc.ee_gates import (
        INSIGHTS_DENIED_MESSAGE,
        insights_turing_denied,
        insights_turing_denied_message,
    )
    from tfc.ee_gating import (
        ENTITLEMENT_CHECK_UNAVAILABLE_MESSAGE,
        EEFeature,
        FeatureUnavailable,
        check_ee_feature,
        entitlements_unavailable,
    )
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
    except FeatureUnavailable as exc:
        if entitlements_unavailable(exc):
            # The entitlement service could not answer: retry the stage,
            # don't end a paying organization's report on a denial it never
            # made.
            raise ApplicationError(
                ENTITLEMENT_CHECK_UNAVAILABLE_MESSAGE,
                type="CapabilityUnavailable",
            ) from None
        raise ApplicationError(
            refusal_message(exc, INSIGHTS_DENIED_MESSAGE),
            LICENSE_CODE,
            type="CapabilityDenied",
            non_retryable=True,
        ) from None
    if insights_turing_denied(org_id):
        raise ApplicationError(
            insights_turing_denied_message(),
            LICENSE_CODE,
            type="CapabilityDenied",
            non_retryable=True,
        )


def _licensed(fn):
    def call(run_id, *args):
        require_license(run_id)
        return fn(run_id, *args)

    return call


NOT_IN_BUILD = "Insights isn't available in this build."
PIPELINE_IMPORT_FAILED = "Insights couldn't load in this build."
# Missing-path errors are quiet only on builds that intentionally omit Insights.
_PIPELINE_MODULES = frozenset(
    {"ee", "ee.agenthub", "ee.agenthub.insights", "ee.agenthub.insights.pipeline"}
)


def _pipeline(name: str):
    """Only a confirmed OSS build without the optional package is a refusal.

    A missing stage in an installed package, or a missing package on an
    image that promises Insights, is a fault. The interceptor reports it.
    """
    from tfc.capabilities import service
    from tfc.capabilities.registry import FEATURE_INSIGHTS
    from tfc.ee_loader import _is_oss_mode, has_ee
    from tfc.licensing.types import DeploymentFlavor

    try:
        import ee.agenthub.insights.pipeline as pipeline

        return getattr(pipeline, name)
    except Exception as exc:  # noqa: BLE001 - any other import failure is a fault
        if (
            isinstance(exc, ModuleNotFoundError)
            and exc.name in _PIPELINE_MODULES
            and service.is_configured()
            and service.get_deployment_flavor() == DeploymentFlavor.OSS
            and _is_oss_mode()
            and FEATURE_INSIGHTS.oss_locked
        ):
            try:
                # Bootstrap can fall back to OSS when the EE usage import fails.
                absent = not has_ee("ee.usage") and not has_ee("ee.agenthub.insights")
            except Exception:  # noqa: BLE001 - package probes execute third-party import code
                absent = False
            if absent:
                raise ApplicationError(
                    NOT_IN_BUILD, type="CapabilityDenied", non_retryable=True
                ) from None
        raise ApplicationError(
            PIPELINE_IMPORT_FAILED,
            type="InsightsPipelineImportError",
            non_retryable=True,
        ) from exc


def _model_error_class():
    try:
        from ee.agenthub.insights.llm import InsightModelError
    except ImportError:
        return None
    return InsightModelError


HEARTBEAT_EVERY = 30  # seconds


async def _heartbeating(work):
    """Await ``work``, heartbeating while it runs, so Temporal notices a lost
    worker within the stage's heartbeat timeout instead of its whole
    start-to-close bound."""
    if not activity.in_activity():
        return await work
    task = asyncio.ensure_future(work)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=HEARTBEAT_EVERY)
            if done:
                return task.result()
            activity.heartbeat()
    finally:
        task.cancel()


async def _run(stage: str, fn, *args, licensed: bool = True):
    from channels.db import database_sync_to_async

    model_error = _model_error_class()
    try:
        return await _heartbeating(
            database_sync_to_async(
                _licensed(fn) if licensed else fn, thread_sensitive=False
            )(*args)
        )
    except ApplicationError:
        raise
    except Exception as exc:
        if model_error is not None and isinstance(exc, model_error):
            # Typed so the workflow shows its text: the model's errors are
            # written for people. Their code, when they have one, rides along
            # as the error's detail.
            code = getattr(exc, "code", "")
            raise ApplicationError(
                str(exc),
                *([code] if code else []),
                type="InsightModelError",
                non_retryable=not exc.unavailable,
            ) from exc
        logger.exception("insight_stage_failed", stage=stage)
        raise ApplicationError("The report could not be completed.") from exc


def _sweep_lost_runs() -> None:
    from django.utils import timezone

    from tracer.models.insights import InsightRun
    from tracer.services.insight_lost_runs import LOST_AFTER, settle_lost_runs

    # Stream quiet runs without loading every report into the worker.
    runs = InsightRun.no_workspace_objects.filter(
        deleted=False,
        status__in=["pending", "running"],
        updated_at__lte=timezone.now() - LOST_AFTER,
    ).order_by("updated_at")
    settle_lost_runs(runs.iterator(chunk_size=100))


@temporal_activity(time_limit=300, queue="tasks_xl", max_retries=0)
def sweep_lost_insight_runs() -> None:
    # TaskRunner schedules use the drop-in adapter's args/kwargs envelope.
    # Settlement must also work after a licence lapses.
    _sweep_lost_runs()


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
    await _run("fail", fail, input.run_id, input.message, input.code, licensed=False)
