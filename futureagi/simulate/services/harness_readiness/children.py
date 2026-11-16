"""The sub-agent evidence row, read from the worker record each call of the agent's runs keeps
(`call_metadata["harness_workers"]`): only the fields that record stores, never what is served from it."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from simulate.models import CallExecution, HostedHarnessJob
from simulate.services.harness_readiness.catalogue import EvidenceCheck, Reading
from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY

_CALLS_READ = 20
# The key the call's worker record is kept under.
WORKERS_KEY = "harness_workers"
# A transfer passes control and never returns; a delegation is whole once it returns.
_HAND_OFFS = frozenset({"delegated", "transferred"})
# A worker's stored end `state` when the trial stopped it before it returned: past its deadline, cancelled, or cut off.
_CUT_SHORT = frozenset(
    {"no_return", "deadline_passed", "cancel_requested", "cancelled", "infrastructure"}
)


def _hand_offs_whole(record: Mapping[str, Any]) -> bool:
    events = [one for one in record.get("events") or [] if isinstance(one, Mapping)]
    back = {one.get("delegation_id") for one in events if one.get("type") == "returned"}
    hand_offs = [one for one in events if one.get("type") in _HAND_OFFS]
    return bool(hand_offs) and all(
        one.get("type") == "transferred" or one.get("delegation_id") in back
        for one in hand_offs
    )


def _observed_ids(record: Mapping[str, Any]) -> set[str]:
    """The stable ids (`id`, the contract id) of the workers a call observed; `worker_id` is scoped to its run."""
    return {
        str(one.get("id"))
        for one in record.get("workers") or []
        if isinstance(one, Mapping) and one.get("observed") and one.get("id")
    }


def children(declared: Any, runs: list[Any]) -> Reading:
    """Sub-agent evidence from this agent's own runs: one evidence line per checklist item, seen or not. Absence is
    unmeasured, never a failure. ``declared`` is the contract's `workers`."""
    declared_ids = {
        str(one["id"])
        for one in declared or []
        if isinstance(one, Mapping) and one.get("id")
    }
    calls = (
        list(
            CallExecution.objects.filter(
                test_execution_id__in=HostedHarnessJob.no_workspace_objects.filter(
                    id__in=runs
                ).values("test_execution_id"),
                call_metadata__has_key=WORKERS_KEY,
            ).order_by("-created_at")[:_CALLS_READ]
        )
        if runs
        else []
    )
    if not declared_ids and not calls:
        return Reading(
            "not_applicable",
            "single_agent",
            "This agent declares no workers and its runs recorded none.",
        )
    if not calls:
        return Reading(
            "not_run", "no_run", "No run of this agent has shown its workers yet."
        )
    metadata = [call.call_metadata or {} for call in calls]
    records = [one.get(WORKERS_KEY) or {} for one in metadata]
    # The same worker under the same id in two runs: ids are stable across runs.
    by_run: dict[str, set[str]] = {}
    for call, record in zip(calls, records, strict=True):
        by_run.setdefault(str(call.test_execution_id), set()).update(
            _observed_ids(record)
        )
    run_ids = list(by_run.values())
    worker_goals = [
        one
        for entry in metadata
        for one in (entry.get("hosted_harness_receipt") or {}).get("sub_goals") or []
        if isinstance(one, Mapping)
        and str(one.get("subject") or "").startswith("worker:")
    ]

    def result(found: bool, applicable: bool = True) -> str:
        return "not_applicable" if not applicable else "seen" if found else "not_seen"

    checks: list[EvidenceCheck] = [
        {
            "check": "stable_ids",
            "result": result(
                any(a & b for i, a in enumerate(run_ids) for b in run_ids[i + 1 :]),
                len(run_ids) > 1,
            ),
        },
        {
            "check": "delegation_and_return",
            "result": result(any(_hand_offs_whole(one) for one in records)),
        },
        {
            "check": "tool_attribution",
            "result": result(
                all(
                    one.get("attribution") != "unattributed"
                    for record in records
                    for one in record.get("events") or []
                    if isinstance(one, Mapping) and one.get("type") == "tool_call"
                )
            ),
        },
        {
            "check": "permissions_exercised",
            "result": result(
                any(
                    str(entry.get("who") or "").startswith("worker:")
                    and (entry.get("refused_actions") or entry.get("committed_actions"))
                    for one in metadata
                    for entry in (one.get(TRIAL_STATE_KEY) or {}).get("state_changes")
                    or []
                    if isinstance(entry, Mapping)
                )
            ),
        },
        {
            "check": "timeout_or_cancel",
            "result": result(
                any(
                    isinstance(one, Mapping) and one.get("state") in _CUT_SHORT
                    for record in records
                    for one in record.get("workers") or []
                )
            ),
        },
        {"check": "worker_verdicts", "result": result(bool(worker_goals))},
        {
            "check": "unmeasured_without_evidence",
            "result": result(any(one.get("held") is None for one in worker_goals)),
        },
    ]
    at = calls[0].created_at.isoformat()
    if any(
        declared_ids <= _observed_ids(record) and _hand_offs_whole(record)
        for record in records
    ):
        return Reading("passed", evidence=checks, checked_at=at)
    if not any(
        isinstance(one, Mapping) and one.get("type") in _HAND_OFFS
        for record in records
        for one in record.get("events") or []
    ):
        return Reading(
            "limited",
            "no_hand_offs",
            "Workers were seen only through their tool calls; the app records no hand-offs.",
            evidence=checks,
            checked_at=at,
        )
    seen = declared_ids & set().union(*(_observed_ids(record) for record in records))
    return Reading(
        "limited",
        "workers_partly_observed",
        f"{len(seen)} of {len(declared_ids)} declared workers were seen in runs; the rest read Not observed.",
        evidence=checks,
        checked_at=at,
    )
