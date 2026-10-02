"""Close usage rows whose run was abandoned in ``processing``.

Only rows whose creator closes them are recovered. The eval paths below create
the row ``processing`` before the eval and write ``success`` or ``error`` after
it; a worker that dies in between (a deploy restart, an OOM, a killed activity)
leaves the row ``processing`` for good.

Everything else is left alone and, where it shares an allowlisted source,
reported. ``synthetic_dataset``, ``simulate_tool_evaluation``, the prompt runs
under ``run_prompt_gen``, the resource checks (no source) and the external-eval
rows under ``tracer`` are created for finished, billed work and never closed. A
row that already holds its eval result finished too: the runner saves the
result before the status.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog
from django.db.models import Q
from django.utils import timezone

from ee.usage.models.usage import APICallLog
from ee.usage.utils.usage_entries import refund_cost_for_api_call
from model_hub.services.stale_work import ExcludedWork, RecoveredWork, StaleWorkBatch
from tfc.constants.api_calls import APICallStatusChoices
from tfc.utils.error_codes import get_error_message

logger = structlog.get_logger(__name__)

# Sources whose every creator closes the row, success or error (``tracer`` is
# narrowed below). Each row lives no longer than the work that creates and
# closes it; the age is past that ceiling, so a row this old has no run left to
# close it.
STALE_AFTER_BY_SOURCE: dict[str, timedelta] = {
    # SDK and Protect evals: a request, or RunEvaluationWorkflow's 12 h activity.
    "standalone_v2": timedelta(hours=24),
    # run_eval_func: a request, or run_eval_func_task's 1 h activity.
    "eval_playground": timedelta(hours=24),
    # run_eval_func inside simulation activities (at most 3 h).
    "simulate": timedelta(hours=24),
    # Observe evals (tracer/utils/eval.py): the 30 min run_entry activity;
    # legacy paths the 12 h drop-in activity ceiling.
    "tracer": timedelta(hours=24),
    "tracer_composite": timedelta(hours=24),
    # One row per evaluated cell, inside the 1 h dataset eval activity or a
    # 12 h experiment activity.
    "dataset_evaluation": timedelta(hours=24),
    "experiment": timedelta(hours=24),
}


# A shared source is recovered only for rows carrying the key its closing
# creator always writes. ``tracer`` rows from tracer/utils/eval.py name their
# custom eval config; tracer/utils/external_eval.py logs the external platform's
# config under the same source, emits its usage event at once and never closes
# the row.
_CLOSING_CREATOR_KEY_BY_SOURCE = {"tracer": "custom_eval_config_id"}

EXCLUDED_CREATOR_NEVER_CLOSES = "creator_never_closes"
EXCLUDED_HOLDS_RESULT = "holds_result"

# Rows read per query while looking for eligible ones past excluded ones.
_SCAN_PAGE = 500

# Sources whose error path hands a wallet deduction back
# (EvaluationRunner._handle_api_call_status). The SDK, run_eval_func and Observe
# error paths close the row without a refund.
_REFUNDED_ON_ERROR = frozenset({"dataset_evaluation", "experiment"})


@dataclass(frozen=True)
class RecoveredUsageRow:
    id: int
    source: str
    organization_id: str
    created_at: datetime
    refunded: bool


@dataclass(frozen=True)
class ExcludedUsageRow:
    """A ``processing`` row past its age that recovery leaves as it is."""

    id: int
    source: str
    organization_id: str
    created_at: datetime
    reason: str


@dataclass(frozen=True)
class UsageRecovery:
    recovered: list[RecoveredUsageRow] = field(default_factory=list)
    excluded: list[ExcludedUsageRow] = field(default_factory=list)


@dataclass(frozen=True)
class StaleUsageSelection:
    eligible: list[APICallLog]
    excluded: list[ExcludedUsageRow]


def _stale_after(source: str, older_than: timedelta | None) -> timedelta:
    # An operator may wait longer than the source's ceiling, never less.
    floor = STALE_AFTER_BY_SOURCE[source]
    return max(floor, older_than) if older_than else floor


def _decoded_config(config: object) -> dict | None:
    """The row's config as a dict: most rows hold it as a JSON string."""
    if isinstance(config, dict):
        return config
    if not isinstance(config, str) or not config:
        return None
    try:
        decoded = json.loads(config)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _exclusion_reason(row: APICallLog) -> str | None:
    """Why recovery must leave ``row`` alone, or None when it may close it."""
    config = _decoded_config(row.config)
    closing_key = _CLOSING_CREATOR_KEY_BY_SOURCE.get(row.source)
    if closing_key and closing_key not in (config or {}):
        return EXCLUDED_CREATOR_NEVER_CLOSES
    # Every error path writes its output and ``error`` in one save (the runner's
    # writes no output at all), so an output object on a ``processing`` row is a
    # result whose status write never landed. A string ``output`` is the
    # template's output type, copied in when the row was created.
    if isinstance((config or {}).get("output"), dict):
        return EXCLUDED_HOLDS_RESULT
    return None


def _excluded(row: APICallLog, reason: str) -> ExcludedUsageRow:
    return ExcludedUsageRow(
        id=row.id,
        source=row.source,
        organization_id=str(row.organization_id),
        created_at=row.created_at,
        reason=reason,
    )


def find_stale_usage_rows(
    *,
    sources: Collection[str],
    older_than: timedelta | None,
    limit: int,
    now: datetime,
) -> StaleUsageSelection:
    """Up to ``limit`` recoverable rows past their source's age, oldest first,
    and the excluded rows met on the way.

    ``created_at`` is the start of the run: nothing writes the row between its
    creation and the outcome. Excluded rows stay ``processing``, so the read
    pages past them instead of stopping at the oldest ``limit`` rows.
    """
    selection = StaleUsageSelection(eligible=[], excluded=[])
    if not sources:
        # An empty Q() matches every processing row, finished work included.
        return selection
    stale = Q()
    for source in sources:
        stale |= Q(source=source, created_at__lt=now - _stale_after(source, older_than))
    candidates = APICallLog.no_workspace_objects.filter(
        stale, status=APICallStatusChoices.PROCESSING.value
    ).order_by("created_at", "id")
    after = Q()
    while len(selection.eligible) < limit:
        page = list(candidates.filter(after)[:_SCAN_PAGE])
        for row in page:
            reason = _exclusion_reason(row)
            if reason:
                selection.excluded.append(_excluded(row, reason))
            elif len(selection.eligible) < limit:
                selection.eligible.append(row)
        if len(page) < _SCAN_PAGE:
            break
        last = page[-1]
        after = Q(created_at__gt=last.created_at) | Q(
            created_at=last.created_at, id__gt=last.id
        )
    return selection


def _with_interrupted_output(config: object, reason: str) -> object:
    """The row's config with the error output the eval paths write.

    Most rows hold their config as a JSON string inside the JSON field; keep
    whichever encoding the row has. A config that is not a JSON object is kept
    as it is, and the row still closes.
    """
    decoded = _decoded_config(config)
    if decoded is None:
        return config
    with_output = {**decoded, "output": {"output": None, "reason": reason}}
    return (
        with_output
        if isinstance(config, dict)
        else json.dumps(with_output, default=str)
    )


def _already_refunded(rows: Collection[APICallLog]) -> set[str]:
    """Ids of ``rows`` whose wallet deduction already has a refund, in one read.

    ``refund_parent_id`` has no index; the refund row carries its parent's
    organization, so the read goes through the organization index.
    """
    owing = [
        row
        for row in rows
        if row.source in _REFUNDED_ON_ERROR and row.deducted_cost > 0
    ]
    if not owing:
        return set()
    return set(
        APICallLog.no_workspace_objects.filter(
            organization_id__in={row.organization_id for row in owing},
            refund_parent_id__in=[str(row.id) for row in owing],
        ).values_list("refund_parent_id", flat=True)
    )


def _owes_refund(row: APICallLog, already_refunded: set[str]) -> bool:
    """Whether the source's own error path would refund this row now.

    Postpaid rows deduct nothing, so only legacy wallet rows qualify, and a row
    already refunded is not refunded again. A dataset eval preview
    (``process_eval_for_single_row``) logs under ``dataset_evaluation`` with
    ``preview`` set, and its error path never refunds.
    """
    config = _decoded_config(row.config) or {}
    return (
        row.source in _REFUNDED_ON_ERROR
        and row.deducted_cost > 0
        and not config.get("preview")
        and str(row.id) not in already_refunded
    )


def _recovered(row: APICallLog, *, refunded: bool) -> RecoveredUsageRow:
    return RecoveredUsageRow(
        id=row.id,
        source=row.source,
        organization_id=str(row.organization_id),
        created_at=row.created_at,
        refunded=refunded,
    )


def close_stale_usage_row(
    row: APICallLog, *, now: datetime, owes_refund: bool
) -> RecoveredUsageRow | None:
    """Close ``row`` the way its source closes a failed run.

    Every eval path sets ``error`` and bills nothing: usage is billed by the
    event emitted on success, and a failed run emits none. The SDK,
    run_eval_func and Observe paths also put the reason in ``config.output``;
    recovery does that for every row, so the eval log says why. The dataset
    and experiment path also calls ``refund_cost_for_api_call``, which records
    the return of a wallet deduction; recovery does the same for those sources
    (``owes_refund``) and no others.

    The write is guarded on ``processing``: a run that closes the row in the
    meantime keeps its outcome, and the row is not reported.
    """
    reason = get_error_message("RUN_INTERRUPTED")
    closed = APICallLog.no_workspace_objects.filter(
        id=row.id, status=APICallStatusChoices.PROCESSING.value
    ).update(
        status=APICallStatusChoices.ERROR.value,
        config=_with_interrupted_output(row.config, reason),
        updated_at=now,
    )
    if not closed:
        return None
    refunded = (
        owes_refund
        and refund_cost_for_api_call(row, config={"reason": reason}) is not None
    )
    return _recovered(row, refunded=refunded)


def recover_stale_usage_rows(
    *,
    apply: bool,
    sources: Collection[str] | None = None,
    older_than: timedelta | None = None,
    limit: int,
) -> UsageRecovery:
    """Close (or, without ``apply``, list) up to ``limit`` abandoned rows, and
    report the excluded rows read on the way."""
    now = timezone.now()
    selection = find_stale_usage_rows(
        sources=STALE_AFTER_BY_SOURCE.keys() if sources is None else sources,
        older_than=older_than,
        limit=limit,
        now=now,
    )
    if selection.excluded:
        logger.info(
            "stale_usage_rows_excluded",
            excluded=len(selection.excluded),
            reasons=sorted({row.reason for row in selection.excluded}),
        )
    already_refunded = _already_refunded(selection.eligible)
    if not apply:
        # ``refunded`` then says what an apply would refund.
        return UsageRecovery(
            recovered=[
                _recovered(row, refunded=_owes_refund(row, already_refunded))
                for row in selection.eligible
            ],
            excluded=selection.excluded,
        )
    recovered = []
    for row in selection.eligible:
        try:
            closed = close_stale_usage_row(
                row, now=now, owes_refund=_owes_refund(row, already_refunded)
            )
        except Exception:
            # One unreadable row must not keep the rest open; the next tick
            # retries it.
            logger.exception("stale_usage_row_close_failed", usage_row_id=row.id)
            continue
        if closed:
            recovered.append(closed)
    logger.info(
        "stale_usage_rows_closed",
        candidates=len(selection.eligible),
        closed=len(recovered),
    )
    return UsageRecovery(recovered=recovered, excluded=selection.excluded)


def recover_stale_usage_work(
    *,
    apply: bool,
    sources: Collection[str],
    older_than: timedelta | None,
    limit: int,
) -> StaleWorkBatch:
    """``recover_stale_usage_rows`` as the stale-work recovery reports it."""
    recovery = recover_stale_usage_rows(
        apply=apply, sources=sources, older_than=older_than, limit=limit
    )
    return StaleWorkBatch(
        recovered=[
            RecoveredWork(
                source=row.source,
                organization_id=row.organization_id,
                dataset_id=None,
                items=1,
                refunds=int(row.refunded),
            )
            for row in recovery.recovered
        ],
        excluded=[
            ExcludedWork(
                source=row.source,
                unit_id=str(row.id),
                organization_id=row.organization_id,
                dataset_id=None,
                reason=row.reason,
                items=1,
            )
            for row in recovery.excluded
        ],
    )
