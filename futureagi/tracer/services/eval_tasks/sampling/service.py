"""Atomic cohort freezing and batched late-row materialization."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import datetime
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import Prefetch, Q, prefetch_related_objects

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_sampling import EvalSamplingCohort, EvalSamplingMember
from tracer.models.eval_task import EvalTask, RunType
from tracer.services.eval_tasks.sampling.policy import (
    InvalidSamplingPolicy,
    policy_version,
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


class SamplingPreviewTooLarge(ValueError):
    pass


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
    version = policy_version(
        policy,
        project_id=str(task.project_id),
        row_type=task.row_type,
        filters=task.filters or {},
    )
    return policy, version


def member_metadata(
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


def preview_policy(
    task: EvalTask, *, now: datetime | None = None, evaluator_count: int = 0
) -> dict:
    now = now or sampling_clock()
    policy, version = task_policy(task, now=now)
    cells = []
    query = SamplingQuery(task, policy, version, now)
    for block in stream_query(*query.preview()):
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
        "estimated_cost": None,
        "cost_note": "Cost is unavailable: evaluator token usage and provider pricing are not fixed by this policy.",
        "population_note": "Eligible units are counted once in their first matching stratum. Missing IDs follow the policy. Closed samples freeze when the task first reconciles; this preview is not a reservation.",
    }


def prepare_live_members(
    task: EvalTask,
    policy: dict,
    version: str,
    cohort: EvalSamplingCohort,
    now: datetime,
    floor: datetime | None,
) -> list[dict]:
    """Freeze new-unit decisions from full snapshots, not first-day witnesses.

    Discovery streams physical-arrival days. Each finite page of unseen unit
    IDs is classified across every already-eligible row of those units before
    any decision is persisted. Thus later day/page discovery cannot introduce
    a different priority or group witness. Earlier polls' decisions stay fixed.
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
                EvalSamplingMember.objects.bulk_create(
                    list(decisions.values()), batch_size=1000
                )
    return windows


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
    # Transfer only selected closed-cohort rows, not the unsampled population.
    # Both the unit count and encoded parameter size are finite for custom IDs.
    batch = []
    byte_count = 0
    for unit in (
        EvalSamplingMember.objects.filter(cohort=cohort, selected=True)
        .values_list("unit_id", flat=True)
        .iterator(chunk_size=1000)
    ):
        size = len(unit.encode("utf-8")) * 2 + 8
        if batch and (len(batch) >= 1000 or byte_count + size > 65536):
            yield from stream_query(
                *SamplingQuery(task, policy, version, now).rows(
                    arrival_floor=floor, unit_ids=batch
                )
            )
            batch, byte_count = [], 0
        batch.append(unit)
        byte_count += size
    if batch:
        yield from stream_query(
            *SamplingQuery(task, policy, version, now).rows(
                arrival_floor=floor, unit_ids=batch
            )
        )


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

    # Lock serializes pollers and policy edits. A failed/partial CH stream rolls
    # back the entire cohort and cursor, not a misleading first page of winners.
    with transaction.atomic():
        task = (
            EvalTask.objects.select_for_update(of=("self",))
            .select_related("project")
            .get(pk=task.pk)
        )
        list(task.evals.select_for_update(of=("self",)).all())
        prefetch_related_objects(
            [task],
            Prefetch(
                "evals",
                queryset=CustomEvalConfig.objects.select_related("eval_template"),
            ),
        )
        now = sampling_clock()
        policy, version = task_policy(task, now=now)
        cohort, cohort_created = EvalSamplingCohort.objects.get_or_create(
            task=task,
            policy_version=version,
            defaults={
                "policy": policy,
                "frozen_at": now,
                "selection_context": {
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
                },
            },
        )
        if policy["mode"] == "closed" and not cohort.complete:
            for block in stream_query(
                *SamplingQuery(task, policy, version, cohort.frozen_at).members()
            ):
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
            cohort.complete = True
            cohort.save(update_fields=["complete"])
        full = (
            cohort_created
            or task.run_type != RunType.CONTINUOUS
            or task.continuous_cursor is None
            or _continuous_requires_full_reclassification(task)
        )
        floor = None if full else task.continuous_cursor
        before = _live_count(task)
        stale = EvalLogger.objects.filter(
            eval_task_id=str(task.id), status=EvalEntryStatus.PENDING
        ).filter(
            Q(output_metadata__isnull=True)
            | Q(output_metadata___task_selection__sampling__policy_version__isnull=True)
            | ~Q(output_metadata___task_selection__sampling__policy_version=version)
        )
        result = ReconcileResult(dropped=stale.update(deleted=True, deleted_at=now))
        witnesses = TraceWitnessReader(task)
        live_windows = (
            prepare_live_members(task, policy, version, cohort, now, floor)
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
            materialize_pending(task, metadata.keys(), **materialize_options)
            requeued, dropped = _requeue_and_drop(
                task,
                resolved=ResolvedRowSet(tuple(candidates), tuple(metadata), full),
                scope_to_candidates=True,
            )
            result.requeued += requeued
            result.dropped += dropped
        current_evals = task.evals.values_list("id", flat=True)
        result.dropped += (
            EvalLogger.objects.filter(
                eval_task_id=str(task.id), status=EvalEntryStatus.PENDING
            )
            .exclude(custom_eval_config_id__in=current_evals)
            .update(deleted=True, deleted_at=now)
        )
        from tracer.services.eval_tasks.sampling.pending import revalidate_pending

        result.dropped += revalidate_pending(
            task, policy, version, cohort, sampling_clock()
        )
        result.created = _live_count(task) - before + result.dropped
        if task.run_type == RunType.CONTINUOUS:
            from tracer.services.eval_tasks.cursor_policy import (
                CONTINUOUS_CURSOR_OVERLAP,
            )

            parked = now - CONTINUOUS_CURSOR_OVERLAP
            if task.continuous_cursor is None or parked > task.continuous_cursor:
                EvalTask.objects.filter(pk=task.pk).update(continuous_cursor=parked)
        _stamp_reclassified_revision(task)
        return result
