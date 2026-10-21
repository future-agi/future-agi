"""Atomic cohort freezing and batched late-row materialization."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import datetime
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import Prefetch, Q, prefetch_related_objects
from django.utils import timezone

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_sampling import EvalSamplingCohort, EvalSamplingMember
from tracer.models.eval_task import EvalTask, RunType
from tracer.services.eval_tasks.sampling.policy import (
    InvalidSamplingPolicy,
    policy_version,
    validate_policy,
)
from tracer.services.eval_tasks.sampling.query import SamplingQuery, stream_query
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
        "sampling_unit": policy["sampling_unit"],
        "sampling_unit_id": member.unit_id,
        "stratum": member.stratum,
        "inclusion_probability": member.inclusion_probability,
        "population_size": member.population_size,
        "window": policy["window"],
    }


def preview_policy(
    task: EvalTask, *, now: datetime | None = None, evaluator_count: int = 0
) -> dict:
    now = now or timezone.now()
    policy, version = task_policy(task, now=now)
    cells = []
    for block in stream_query(*SamplingQuery(task, policy, version, now).preview()):
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


def iter_selected_row_blocks(
    task: EvalTask,
    policy: dict,
    version: str,
    cohort: EvalSamplingCohort,
    now: datetime,
    floor: datetime | None,
) -> Iterator[list[tuple]]:
    if policy["mode"] == "live":
        yield from stream_query(
            *SamplingQuery(task, policy, version, now).rows(arrival_floor=floor)
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
        _advance_continuous_cursor,
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
        now = timezone.now()
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
        for block in iter_selected_row_blocks(
            task, policy, version, cohort, now, floor
        ):
            if policy["mode"] == "live":
                # Persist rejected decisions too. A late span with a different
                # category must not turn an already-skipped contact into a sample.
                by_unit = {str(row[1]): row for row in block}
                EvalSamplingMember.objects.bulk_create(
                    [
                        EvalSamplingMember(
                            cohort=cohort,
                            unit_key=unit_key(unit),
                            unit_id=unit,
                            stratum=str(row[2]),
                            selected=bool(row[4]),
                            inclusion_probability=float(row[3]),
                            population_size=None,
                        )
                        for unit, row in by_unit.items()
                    ],
                    batch_size=1000,
                    ignore_conflicts=True,
                )
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
                resolved=ResolvedRowSet(tuple(candidates), tuple(metadata), False),
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
        result.created = _live_count(task) - before + result.dropped
        _advance_continuous_cursor(task, now)
        _stamp_reclassified_revision(task)
        return result
