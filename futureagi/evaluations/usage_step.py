"""The step an evaluation's usage events are keyed to, and its event id.

The billing consumer keeps one usage event per event id. An evaluation step
that a worker runs again after a restart or a retry derives the same id, so the
step is billed once.
"""

from typing import TypedDict

import structlog

logger = structlog.get_logger(__name__)


class UsageStep(TypedDict):
    """One evaluation step, as its usage events are keyed.

    ``evaluated`` is what was evaluated (a row and its columns, a span, trace
    or session, a request), ``run`` the run that evaluated it and
    ``evaluator`` what evaluated it. Each is a value or a tuple of values.
    """

    evaluated: object | tuple[object, ...] | None
    run: object | tuple[object, ...] | None
    evaluator: object | tuple[object, ...] | None


def step_event_id(step: UsageStep | None) -> str | None:
    """The stable usage event id of ``step``, or None when it has none.

    The same step always gets the same id, so a step that a worker runs again
    emits the events it emitted before and is billed once. None when there is
    no step, when a part of it is unknown, or when the id cannot be derived;
    the caller's event then gets a fresh event id and is billed. This is
    fail-open: an ee that is absent or cannot derive step ids gives None
    quietly, and any other failure is logged with its stack and gives None, so
    the charge always goes ahead.
    """
    if step is None:
        return None
    try:
        from ee.usage.schemas.events import stable_evaluation_event_id
    except ImportError:
        return None
    try:
        return stable_evaluation_event_id(**step)
    except Exception:
        log_unreadable_step()
        return None


def error_localizer_step(step: UsageStep | None, source: str) -> UsageStep | None:
    """The step the error localization of evaluation step ``step`` is keyed
    to, for a localization task of ``source`` ("playground" or "simulate").

    The same thing in the same run, evaluated by the step's evaluator as the
    error localizer of that task source. A retry that makes the step's tasks
    again localizes them under the ids of the first attempt and is billed
    once; each task source keeps its own id, so a first run is billed for each
    of its tasks. None when ``step`` has no id or cannot be read; the
    localization then gets a fresh event id and is billed.
    """
    try:
        if step is None or step_event_id(step) is None:
            return None
        evaluator = step["evaluator"]
        parts = evaluator if isinstance(evaluator, tuple | list) else (evaluator,)
        return {**step, "evaluator": (*parts, "error_localizer", source)}
    except Exception:
        log_unreadable_step()
        return None


def log_unreadable_step() -> None:
    """Log the exception being handled: a step's identity could not be read,
    so its usage events get fresh event ids. Call it from an ``except``
    block."""
    logger.warning("evaluation_usage_step_id_failed", exc_info=True)


def activity_workflow_run() -> str | None:
    """The workflow run of the Temporal activity running this code: the same
    across the activity's retries, new for a new dispatch. None outside an
    activity or when it cannot be read."""
    try:
        from temporalio import activity
    except ImportError:
        return None
    try:
        if not activity.in_activity():
            return None
        return activity.info().workflow_run_id or None
    except Exception:
        log_unreadable_step()
        return None
