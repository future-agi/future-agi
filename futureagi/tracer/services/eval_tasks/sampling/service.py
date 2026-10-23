"""Cohort freezing and batched late-row materialization under a lease.

No PostgreSQL transaction or task/evaluator row lock spans a ClickHouse
stream. A reconcile pass takes a short-lived cohort lease, then writes each
bounded page in its own short transaction that re-proves the lease token and
the task revision (``_Fence``). A closed cohort is certified (``complete``)
only after its whole membership stream has been written; an interrupted
freeze is discarded, never read as a partial quota.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.db.models import Prefetch, Q, prefetch_related_objects
from django.db.models.functions import Now

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_sampling import EvalSamplingCohort, EvalSamplingMember
from tracer.models.eval_task import EvalTask, RunType
from tracer.selectors.eval_tasks.row_resolver import EvalTaskSelectionRejected
from tracer.services.eval_tasks.sampling import stats
from tracer.services.eval_tasks.sampling.errors import (
    SamplingFilterRejected,
    SamplingLeaseLost,
    SamplingReadPressure,
    SamplingReconcileBusy,
)
from tracer.services.eval_tasks.sampling.policy import (
    InvalidSamplingPolicy,
    parse_time,
    policy_version,
    require_policy_time_authority,
    selection_seed,
    validate_live_filter_sources,
    validate_policy,
)
from tracer.services.eval_tasks.sampling.query import (
    SamplingQuery,
    live_scan_windows,
    sampling_clock,
    stream_query,
)
from tracer.services.eval_tasks.sampling.witnesses import TraceWitnessReader

if TYPE_CHECKING:
    from tracer.services.eval_tasks.reconciler import ReconcileResult

logger = logging.getLogger(__name__)
# Indirection so tests can advance a controlled clock instead of sleeping.
_sleep = time.sleep


class SamplingPreviewTooLarge(ValueError):
    pass


def _bounded_seconds(name: str, default: int, *, low: int, high: int) -> int:
    value = getattr(settings, name, default)
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}.")
    return value


def closed_settle_margin() -> timedelta:
    """Ingestion/replication allowance applied when a closed cohort freezes.

    The freeze counts only rows that arrived at least this long before the
    ClickHouse clock, and waits until that cutoff is at least this long after
    the window end. In-window rows arriving shortly after the window closes
    are counted, and a replica lagging by less than the margin reads the same
    population as the primary.
    """

    return timedelta(
        seconds=_bounded_seconds(
            "EVAL_SAMPLING_CLOSED_SETTLE_SECONDS", 15, low=0, high=3600
        )
    )


def reconcile_lease_duration() -> timedelta:
    return timedelta(
        seconds=_bounded_seconds("EVAL_SAMPLING_LEASE_SECONDS", 300, low=30, high=3600)
    )


def unit_key(unit_id: str) -> str:
    return hashlib.sha256(unit_id.encode()).hexdigest()


def task_policy(task: EvalTask, *, now: datetime) -> tuple[dict, str]:
    policy = validate_policy(
        task.sampling_policy,
        row_type=task.row_type,
        now=now,
        contact_attribute=task.project.sampling_contact_attribute,
    )
    if policy is None:
        raise InvalidSamplingPolicy("A sampling policy is required.")
    if policy["mode"] == "live" and task.run_type != RunType.CONTINUOUS:
        raise InvalidSamplingPolicy(
            "Live percentage policies require a continuous task."
        )
    validate_live_filter_sources(policy, task.filters)
    require_policy_time_authority(task.filters)
    version = policy_version(
        policy,
        project_id=str(task.project_id),
        row_type=task.row_type,
        filters=task.filters or {},
    )
    return policy, version


def coordination_context(policy: dict, *, project_id: str) -> dict | None:
    """Provenance of a coordinated (shared-PRN) hash seed; None if independent."""

    if policy.get("coordination", "independent") == "independent":
        return None
    return {
        "mode": policy["coordination"],
        "panel": policy.get("panel", ""),
        "prn_start": policy.get("prn_start", 0),
        "seed": selection_seed(policy, version="", project_id=project_id),
    }


def member_metadata(
    member: EvalSamplingMember, policy: dict, version: str, *, frozen_at: datetime
) -> dict:
    metadata = _member_metadata(member, policy, version, frozen_at=frozen_at)
    if policy.get("coordination", "independent") != "independent":
        # Results of coordinated policies share one random number per unit,
        # so their inclusion events are dependent across tasks: say so.
        metadata["coordination"] = policy["coordination"]
    return metadata


def _member_metadata(
    member: EvalSamplingMember, policy: dict, version: str, *, frozen_at: datetime
) -> dict:
    return {
        "policy_version": version,
        "cohort_id": str(member.cohort_id),
        "cohort_frozen_at": frozen_at.isoformat()
        if policy["mode"] == "closed"
        else None,
        "membership_mode": policy["mode"],
        "sampling_unit": member.unit_id.split(":", 1)[0],
        "requested_sampling_unit": policy["sampling_unit"],
        "missing_id_fallback": member.unit_id.split(":", 1)[0]
        != policy["sampling_unit"],
        "sampling_unit_id": member.unit_id,
        "stratum": member.stratum,
        "inclusion_probability": member.inclusion_probability,
        "population_size": member.population_size,
        "window": policy["window"],
    }


PLANNING_CONFIDENCE = 0.95
PLANNING_PROPORTION = 0.5


def preview_precision(cells: list[dict]) -> dict:
    """Precision of the previewed design for a pass-rate-like proportion.

    Adds each cell's 95% margin of error (finite-population corrected, at the
    conservative p = 0.5) for its selected units, and summarizes the
    stratified margin and the Kish effect of the design weights 1/p. Within
    each cell p is constant, so expected_units = N*p: closed cells use k/N,
    while live cells use their configured rate, not the realized fraction n/N.
    """

    for cell in cells:
        cell["margin_of_error"] = stats.margin_of_error(
            cell["selected_units"],
            cell["eligible_units"],
            proportion=PLANNING_PROPORTION,
            confidence=PLANNING_CONFIDENCE,
        )
    weighted = [
        (cell["eligible_units"] / cell["expected_units"], cell["selected_units"])
        for cell in cells
        if cell["selected_units"]
    ]
    return {
        "confidence": PLANNING_CONFIDENCE,
        "assumed_proportion": PLANNING_PROPORTION,
        "stratified_margin_of_error": stats.stratified_margin_of_error(
            [
                (cell["eligible_units"], cell["selected_units"], PLANNING_PROPORTION)
                for cell in cells
            ],
            confidence=PLANNING_CONFIDENCE,
        ),
        "kish_effective_n": stats.kish_effective_n(weighted),
        "design_effect": stats.design_effect(weighted),
        "unsampled_strata": sum(
            1 for cell in cells if cell["eligible_units"] and not cell["selected_units"]
        ),
    }


def preview_policy(
    task: EvalTask, *, now: datetime | None = None, evaluator_count: int = 0
) -> dict:
    now = now or sampling_clock()
    policy, version = task_policy(task, now=now)
    cells = []
    query = SamplingQuery(task, policy, version, now)
    try:
        statement = query.preview()
    except EvalTaskSelectionRejected:
        raise
    except ValueError as exc:
        # Compiling the row filters is pure validation, not a dependency
        # failure: name the unsupported filter instead of reporting outage.
        raise SamplingFilterRejected(
            f"These task filters cannot define a sampling population: {exc}"
        ) from exc
    for block in stream_query(*statement):
        for (
            name,
            population,
            row_count,
            expected,
            expected_rows,
            selected,
            selected_rows,
        ) in block:
            cells.append(
                {
                    "stratum": name,
                    "eligible_units": population,
                    "eligible_rows": row_count,
                    "expected_units": expected,
                    "expected_rows": expected_rows,
                    "selected_units": selected,
                    "selected_rows": selected_rows,
                }
            )
            if len(cells) > 1000:
                raise SamplingPreviewTooLarge(
                    "Preview exceeds 1000 strata. Narrow the group-by attributes or window."
                )
    precision = preview_precision(cells)
    return {
        "policy_version": version,
        "sampling_unit": policy["sampling_unit"],
        "window": policy["window"],
        "preview_window": query.scan_window,
        "mode": policy["mode"],
        "as_of": now,
        "strata": cells,
        "evaluator_count": evaluator_count,
        "expected_evaluations": sum(cell["expected_rows"] for cell in cells)
        * evaluator_count
        if evaluator_count
        else None,
        "selected_evaluations": sum(cell["selected_rows"] for cell in cells)
        * evaluator_count
        if evaluator_count
        else None,
        "precision": precision,
        "estimated_cost": None,
        "cost_note": "Cost is unavailable: evaluator token usage and provider pricing are not fixed by this policy.",
        "population_note": "Eligible units are counted once in their first matching stratum. Missing IDs follow the policy. Closed samples freeze when the task first reconciles; this preview is not a reservation.",
    }


class _Fence:
    """Single-writer proof for one reconcile pass.

    Every write runs in its own short transaction that locks the task and its
    evaluator rows (serializing with edits, exactly like the legacy apply),
    proves the task revision this pass started from and renews the cohort
    lease only while it still holds the token. A pass whose lease expired, or
    whose task was edited, raises before it writes anything else.
    """

    def __init__(self, task_id, revision: str, cohort_id, token: uuid.UUID) -> None:
        self.task_id, self.revision = task_id, revision
        self.cohort_id, self.token = cohort_id, token

    @contextmanager
    def write(self) -> Iterator[EvalTask]:
        from tracer.services.eval_tasks.reconciler import (
            EvalTaskRevisionChanged,
            _task_revision,
        )

        with transaction.atomic():
            task = _locked_task(self.task_id)
            if _task_revision(task) != self.revision:
                raise EvalTaskRevisionChanged(
                    "Evaluation task changed while its sampling pass was running."
                )
            renewed = EvalSamplingCohort.objects.filter(
                pk=self.cohort_id, lease_token=self.token
            ).update(lease_expires_at=Now() + reconcile_lease_duration())
            if renewed != 1:
                raise SamplingLeaseLost(
                    "Another sampling reconcile took over this cohort. Retry."
                )
            yield task

    def release(self) -> None:
        EvalSamplingCohort.objects.filter(
            pk=self.cohort_id, lease_token=self.token
        ).update(lease_token=None, lease_expires_at=None)


def _locked_task(task_id) -> EvalTask:
    task = (
        EvalTask.objects.select_for_update(of=("self",))
        .select_related("project")
        .get(pk=task_id)
    )
    list(task.evals.select_for_update(of=("self",)).all())
    prefetch_related_objects(
        [task],
        Prefetch(
            "evals", queryset=CustomEvalConfig.objects.select_related("eval_template")
        ),
    )
    return task


WriteScope = Callable[[], AbstractContextManager]


def prepare_live_members(
    task: EvalTask,
    policy: dict,
    version: str,
    cohort: EvalSamplingCohort,
    now: datetime,
    floor: datetime | None,
    *,
    write: WriteScope = transaction.atomic,
) -> list[dict]:
    """Freeze new-unit decisions from full snapshots, not first-day witnesses.

    Discovery streams physical-arrival days. Each finite page of unseen unit
    IDs is classified across every already-eligible row of those units before
    any decision is persisted. Thus later day/page discovery cannot introduce
    a different priority or group witness. Earlier polls' decisions stay fixed.
    A page is written only after its complete proof; a failed or bounded-out
    proof writes nothing for its units.
    """
    from tracer.services.eval_tasks.sampling.unit_scope import unit_batches

    windows = list(live_scan_windows(task, policy, now, floor))
    for window in windows:
        for block in stream_query(
            *SamplingQuery(task, policy, version, now, scan_window=window).rows(
                arrival_floor=floor
            )
        ):
            for units in unit_batches(row[1] for row in block):
                existing = set(
                    EvalSamplingMember.objects.filter(
                        cohort=cohort, unit_key__in=[unit_key(unit) for unit in units]
                    ).values_list("unit_id", flat=True)
                )
                unseen = [unit for unit in units if unit not in existing]
                if not unseen:
                    continue
                decisions = {}
                for proof in stream_query(
                    *SamplingQuery(task, policy, version, now).live_member_decisions(
                        unseen
                    )
                ):
                    for unit, stratum, probability, selected in proof:
                        unit = str(unit)
                        if unit not in unseen or unit in decisions:
                            raise ValueError(
                                "Live unit decision escaped or duplicated its candidate scope."
                            )
                        decisions[unit] = EvalSamplingMember(
                            cohort=cohort,
                            unit_key=unit_key(unit),
                            unit_id=unit,
                            stratum=str(stratum),
                            selected=bool(selected),
                            inclusion_probability=float(probability),
                            population_size=None,
                        )
                if set(decisions) != set(unseen):
                    raise ValueError(
                        "Live unit snapshot changed during first admission. Retry the bounded proof."
                    )
                with write():
                    EvalSamplingMember.objects.bulk_create(
                        list(decisions.values()), batch_size=1000
                    )
    return windows


def _selected_member_pages(cohort: EvalSamplingCohort) -> Iterator[list[str]]:
    """Keyset pages of frozen members; no cursor stays open across commits."""
    from tracer.services.eval_tasks.sampling.unit_scope import unit_batches

    last = 0
    while True:
        page = list(
            EvalSamplingMember.objects.filter(cohort=cohort, selected=True, pk__gt=last)
            .order_by("pk")
            .values_list("pk", "unit_id")[:1000]
        )
        if not page:
            return
        last = page[-1][0]
        # Both the unit count and encoded parameter size are finite.
        yield from unit_batches(unit for _pk, unit in page)


def iter_selected_row_blocks(
    task: EvalTask,
    policy: dict,
    version: str,
    cohort: EvalSamplingCohort,
    now: datetime,
    floor: datetime | None,
    *,
    live_windows: list[dict] | None = None,
) -> Iterator[list[tuple]]:
    if policy["mode"] == "live":
        for window in live_windows or []:
            yield from stream_query(
                *SamplingQuery(task, policy, version, now, scan_window=window).rows(
                    arrival_floor=floor
                )
            )
        return
    # Transfer only selected closed-cohort rows, not the unsampled population,
    # and push each page's unit IDs into the physical read so a page never
    # rescans the whole window.
    for batch in _selected_member_pages(cohort):
        yield from stream_query(
            *SamplingQuery(task, policy, version, now).rows(
                arrival_floor=floor, unit_ids=batch
            )
        )


def _await_closed_settle(task: EvalTask) -> datetime:
    """Return a ClickHouse clock reading at which a new closed cohort may freeze.

    Waits (without any transaction) at most twice the configured settle
    margin, which bounds the remaining time because closed windows must
    already have ended when the policy is validated.
    """

    now = sampling_clock()
    margin = closed_settle_margin()
    policy, version = task_policy(task, now=now)
    if policy["mode"] != "closed" or not margin:
        return now
    if EvalSamplingCohort.objects.filter(
        task_id=task.pk, policy_version=version, complete=True
    ).exists():
        return now
    ready_at = parse_time(policy["window"]["end"]) + 2 * margin
    for _attempt in range(3):
        if now >= ready_at:
            return now
        _sleep(min((ready_at - now).total_seconds(), 2 * margin.total_seconds()))
        now = sampling_clock()
    if now >= ready_at:
        return now
    raise SamplingReadPressure(
        "The closed sampling window is still settling. Retry shortly."
    )


def _acquire(
    task_id, now: datetime
) -> tuple[EvalTask, dict, str, EvalSamplingCohort, bool, _Fence]:
    """Short locked snapshot: policy, revision, cohort and its lease."""
    from tracer.services.eval_tasks.reconciler import _task_revision

    token = uuid.uuid4()
    with transaction.atomic():
        task = _locked_task(task_id)
        policy, version = task_policy(task, now=now)
        margin = closed_settle_margin() if policy["mode"] == "closed" else timedelta(0)
        context = {
            "project_id": str(task.project_id),
            "organization_id": str(task.project.organization_id),
            "workspace_id": str(task.project.workspace_id)
            if task.project.workspace_id
            else None,
            "row_type": task.row_type,
            "filters": task.filters or {},
            "algorithm": "sha256-bottom-k-v1"
            if policy["mode"] == "closed"
            else "sha256-bernoulli-v1",
        }
        coordination = coordination_context(policy, project_id=str(task.project_id))
        if coordination is not None:
            context["coordination"] = coordination
        cohort, created = EvalSamplingCohort.objects.get_or_create(
            task=task,
            policy_version=version,
            defaults={
                "policy": policy,
                "frozen_at": now - margin,
                "selection_context": context,
            },
        )
        acquired = (
            EvalSamplingCohort.objects.filter(pk=cohort.pk)
            .filter(Q(lease_token__isnull=True) | Q(lease_expires_at__lt=Now()))
            .update(
                lease_token=token, lease_expires_at=Now() + reconcile_lease_duration()
            )
        )
        if acquired != 1:
            raise SamplingReconcileBusy(
                "Another sampling reconcile is running for this task. Retry later."
            )
        fresh = created
        if policy["mode"] == "closed" and not cohort.complete and not created:
            # An earlier attempt stopped mid-freeze and released or lost its
            # lease. Nothing it wrote was certified: discard it and freeze anew.
            cohort.members.all().delete()
            cohort.frozen_at = now - margin
            cohort.save(update_fields=["frozen_at"])
            fresh = True
        cohort.refresh_from_db()
        fence = _Fence(task.pk, _task_revision(task), cohort.pk, token)
    return task, policy, version, cohort, fresh, fence


def _freeze_closed(
    task: EvalTask,
    policy: dict,
    version: str,
    cohort: EvalSamplingCohort,
    fence: _Fence,
) -> None:
    """Write the whole bottom-k membership, then certify it in one step."""
    try:
        for block in stream_query(
            *SamplingQuery(task, policy, version, cohort.frozen_at).members()
        ):
            with fence.write():
                EvalSamplingMember.objects.bulk_create(
                    [
                        EvalSamplingMember(
                            cohort=cohort,
                            unit_key=unit_key(str(unit)),
                            unit_id=str(unit),
                            stratum=str(stratum),
                            selected=True,
                            inclusion_probability=float(probability),
                            population_size=int(population),
                        )
                        for unit, stratum, probability, population in block
                    ],
                    batch_size=1000,
                )
        with fence.write():
            EvalSamplingCohort.objects.filter(pk=cohort.pk).update(complete=True)
        cohort.complete = True
    except BaseException:
        # A failed or incomplete stream never leaves a first page of winners
        # behind: drop the uncertified cohort (members cascade) if we still
        # own it. If even that cleanup fails, the cohort stays uncertified and
        # the next lease holder discards it before freezing.
        try:
            with transaction.atomic():
                EvalSamplingCohort.objects.filter(
                    pk=cohort.pk, lease_token=fence.token, complete=False
                ).delete()
        except Exception:
            logger.warning("sampling.freeze_cleanup_failed", exc_info=True)
        raise


def reconcile_policy(task: EvalTask) -> ReconcileResult:
    from tracer.models.observation_span import EvalEntryStatus, EvalLogger
    from tracer.selectors.eval_tasks.row_resolver import ResolvedRowSet
    from tracer.services.eval_tasks.entries import materialize_pending
    from tracer.services.eval_tasks.reconciler import (
        ReconcileResult,
        _continuous_requires_full_reclassification,
        _live_count,
        _requeue_and_drop,
        _stamp_reclassified_revision,
    )
    from tracer.services.eval_tasks.sampling.pending import revalidate_pending

    now = _await_closed_settle(task)
    task, policy, version, cohort, fresh, fence = _acquire(task.pk, now)
    try:
        if policy["mode"] == "closed" and not cohort.complete:
            _freeze_closed(task, policy, version, cohort, fence)
        with fence.write() as locked:
            full = (
                fresh
                or locked.run_type != RunType.CONTINUOUS
                or locked.continuous_cursor is None
                or _continuous_requires_full_reclassification(locked)
            )
            floor = None if full else locked.continuous_cursor
            before = _live_count(locked)
            stale = EvalLogger.objects.filter(
                eval_task_id=str(locked.id), status=EvalEntryStatus.PENDING
            ).filter(
                Q(output_metadata__isnull=True)
                | Q(
                    output_metadata___task_selection__sampling__policy_version__isnull=True
                )
                | ~Q(output_metadata___task_selection__sampling__policy_version=version)
            )
            result = ReconcileResult(dropped=stale.update(deleted=True, deleted_at=now))
        witnesses = TraceWitnessReader(task)
        live_windows = (
            prepare_live_members(
                task, policy, version, cohort, now, floor, write=fence.write
            )
            if policy["mode"] == "live"
            else None
        )
        for block in iter_selected_row_blocks(
            task, policy, version, cohort, now, floor, live_windows=live_windows
        ):
            keys = [unit_key(str(row[1])) for row in block]
            members = {
                member.unit_id: member
                for member in EvalSamplingMember.objects.filter(
                    cohort=cohort, unit_key__in=keys
                )
            }
            metadata = {}
            candidates = []
            for row in block:
                identity, unit = str(row[0]), str(row[1])
                candidates.append(identity)
                member = members.get(unit)
                if member is not None and member.selected:
                    metadata[identity] = member_metadata(
                        member, policy, version, frozen_at=cohort.frozen_at
                    )
            trace_witnesses = witnesses.read(metadata.keys())
            materialize_options = {"sampling_metadata": metadata}
            if trace_witnesses:
                materialize_options["trace_filter_witnesses"] = trace_witnesses
            with fence.write() as locked:
                materialize_pending(locked, metadata.keys(), **materialize_options)
                requeued, dropped = _requeue_and_drop(
                    locked,
                    resolved=ResolvedRowSet(tuple(candidates), tuple(metadata), full),
                    scope_to_candidates=True,
                )
            result.requeued += requeued
            result.dropped += dropped
        with fence.write() as locked:
            current_evals = locked.evals.values_list("id", flat=True)
            result.dropped += (
                EvalLogger.objects.filter(
                    eval_task_id=str(locked.id), status=EvalEntryStatus.PENDING
                )
                .exclude(custom_eval_config_id__in=current_evals)
                .update(deleted=True, deleted_at=now)
            )
        result.dropped += revalidate_pending(
            task, policy, version, cohort, sampling_clock(), page_write=fence.write
        )
        with fence.write() as locked:
            result.created = _live_count(locked) - before + result.dropped
            if locked.run_type == RunType.CONTINUOUS:
                from tracer.services.eval_tasks.cursor_policy import (
                    CONTINUOUS_CURSOR_OVERLAP,
                )

                parked = now - CONTINUOUS_CURSOR_OVERLAP
                # Forward-only, conditional on the stored value this pass saw.
                EvalTask.objects.filter(pk=locked.pk).filter(
                    Q(continuous_cursor__isnull=True) | Q(continuous_cursor__lt=parked)
                ).update(continuous_cursor=parked)
            _stamp_reclassified_revision(locked)
        return result
    finally:
        try:
            fence.release()
        except Exception:
            # The lease expires on its own; never mask the pass's outcome.
            logger.warning("sampling.lease_release_failed", exc_info=True)
