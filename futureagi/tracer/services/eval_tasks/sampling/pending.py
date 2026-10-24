"""Prove current pending eligibility independently of incremental discovery."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import datetime
from typing import TYPE_CHECKING

from django.db import transaction

from tracer.models.eval_sampling import EvalSamplingMember
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.services.eval_tasks.sampling.query import (
    BATCH_SIZE,
    SamplingQuery,
    stream_query,
)

if TYPE_CHECKING:
    from tracer.models.eval_sampling import EvalSamplingCohort
    from tracer.models.eval_task import EvalTask


IDENTITY_FIELDS = {
    "spans": "observation_span_id",
    "traces": "trace_id",
    "sessions": "trace_session_id",
}


def read_current_rows(
    task: EvalTask, policy: dict, version: str, ids: list[str], now: datetime
) -> dict[str, str]:
    current = {}
    requested = set(ids)
    for block in stream_query(
        *SamplingQuery(task, policy, version, now).current_rows(ids)
    ):
        for identity, unit in block:
            identity = str(identity)
            if identity not in requested or identity in current:
                raise ValueError(
                    "Pending sampling proof escaped or duplicated its candidate scope."
                )
            current[identity] = str(unit)
    return current


def revalidate_pending(
    task: EvalTask,
    policy: dict,
    version: str,
    cohort: EvalSamplingCohort,
    now: datetime,
    *,
    page_write: Callable[[], AbstractContextManager] = transaction.atomic,
) -> int:
    """Lock and revalidate one bounded pending page per short transaction.

    This also visits older backlog, but each visited ID gets a full-state point
    proof. Thus an unchanged old row stays pending even outside the delta, while
    filter exits, deletions and moves to unselected units cannot reach the drain.
    Completed/errored/running rows and their original provenance are untouched.
    A page's entry locks span its bounded current-state and trace-witness reads,
    so the drain cannot claim a row while its negative proof is in flight.
    Reconcile's page_write fence also holds task/evaluator and cohort locks
    across those reads: configuration edits can wait until page commit/rollback.
    """
    from tracer.services.eval_tasks.sampling.witnesses import TraceWitnessReader

    witnesses = TraceWitnessReader(task)
    identity_field = IDENTITY_FIELDS[task.row_type]
    state = {"last_id": None, "dropped": 0}
    while True:
        with page_write():
            if not _revalidate_page(
                task, policy, version, cohort, now, witnesses, identity_field, state
            ):
                return state["dropped"]


def _revalidate_page(
    task, policy, version, cohort, now, witnesses, identity_field, state
) -> bool:
    from tracer.services.eval_tasks.entries import serialize_trace_witnesses
    from tracer.services.eval_tasks.sampling.service import member_metadata, unit_key

    last_id = state["last_id"]
    query = EvalLogger.objects.filter(
        eval_task_id=str(task.id), status=EvalEntryStatus.PENDING
    )
    if last_id is not None:
        query = query.filter(pk__gt=last_id)
    entries = list(
        query.select_for_update(of=("self",))
        .order_by("pk")
        .only(
            "id",
            identity_field,
            "output_metadata",
        )[:BATCH_SIZE]
    )
    if not entries:
        return False
    state["last_id"] = entries[-1].pk
    ids = list(dict.fromkeys(str(getattr(entry, identity_field)) for entry in entries))
    current = read_current_rows(task, policy, version, ids, now)
    members = {
        member.unit_id: member
        for member in EvalSamplingMember.objects.filter(
            cohort=cohort,
            selected=True,
            unit_key__in=[unit_key(unit) for unit in set(current.values())],
        )
    }
    positive_ids = [identity for identity, unit in current.items() if unit in members]
    by_trace = serialize_trace_witnesses(witnesses.read(positive_ids))
    excluded = []
    changed = []
    for entry in entries:
        member = members.get(current.get(str(getattr(entry, identity_field))))
        if member is None:
            excluded.append(entry.pk)
            continue
        sampling = member_metadata(member, policy, version, frozen_at=cohort.frozen_at)
        metadata = deepcopy(entry.output_metadata or {})
        selection = metadata.setdefault("_task_selection", {})
        selection["sampling"] = sampling
        if str(getattr(entry, identity_field)) in by_trace:
            selection["filter_witnesses"] = by_trace[
                str(getattr(entry, identity_field))
            ]
        if metadata != entry.output_metadata:
            entry.output_metadata = metadata
            changed.append(entry)
    if excluded:
        state["dropped"] += EvalLogger.objects.filter(
            pk__in=excluded, status=EvalEntryStatus.PENDING
        ).update(deleted=True, deleted_at=now)
    if changed:
        EvalLogger.objects.bulk_update(
            changed, ["output_metadata"], batch_size=BATCH_SIZE
        )
    return True
