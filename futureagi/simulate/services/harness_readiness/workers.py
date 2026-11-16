"""Run readiness, per declared worker: which of its tools the world's door holds it to, read from what ALK sealed
(the bundle root's `door.json` and `worker-limits.json`, the tool proxy's `actions.json` and `permissions.json`) and
the agent's own runs. The best a worker reads is Partial: a call made through another worker's address is not held,
only flagged. Which cases a worker holds back is ALK's own per-scenario answer in `worker-limits.json`, read here."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from simulate.models import CallExecution, HostedHarnessJob
from simulate.services.harness_readiness.catalogue import (
    Fix,
    Pairing,
    Status,
    WorkerBoundaryView,
    WorkerBypass,
    WorkerToolView,
)
from simulate.services.harness_readiness.evidence import Evidence, contract_of
from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY

BYPASS_CODE = "worker_channel_bypassed"
_BYPASSES_SHOWN = 5
_CALLS_READ = 50

# Why ALK's run holds a case back (`worker-limits.json` `scenarios.<key>.cause`), as the reason a person reads.
_CAUSE_REASONS = {
    "in_process": "the world's door inside the agent cannot tell the workers' calls apart",
    "shared_worker": "a worker wired as shared cannot be told apart by the world",
    "tool_service_not_fronted": "the agent's tool service does not run behind the world's door",
    "sealed_before_worker_limits": "the environment was sealed before worker limits; rebuild it to hold them",
    "no_tool_proxy": "the agent's tools are not reached through the world's tool proxy",
}
_UNKNOWN_CAUSE = "the world cannot hold the limits this case relies on"

# The status word a worker row shows, by status; a row whose code has its own word reads that instead.
_STATUS_LABELS: dict[Status, str] = {
    "supported": "Supported",
    "partial": "Partial",
    "blocked": "Blocked",
    "unavailable": "Unavailable",
    "untested": "Untested",
    "stale": "Stale",
}
_CODE_LABELS = {
    "held_at_world_cooperative": "Held at the world (cooperative)",
    "in_process_door": "Deadlines held, tools not enforced",
}


@dataclass(frozen=True)
class CaseHold:
    """ALK's sealed answer for one scenario: held back (`held`), clear, or not known (`held` None); the workers it
    names and whether it relies on their limits."""

    held: bool | None
    workers: tuple[str, ...] = ()
    reason: str | None = None
    relies: bool = False
    # The workers whose limits the case relies on and the build cannot hold; None on a seal from before ALK named them.
    unheld: tuple[str, ...] | None = None


_NOT_KNOWN = CaseHold(held=None)
NO_HOLD = CaseHold(held=False)


def bypassed_workers(workers: list[WorkerBoundaryView]) -> dict[str, str]:
    """Each worker a recent run saw reach the world without its own channel, with its row's reason: a case relying on
    its limits is held until it is rebound."""
    return {
        one["worker_id"]: one["reason"]
        for one in workers
        if one["reason_code"] == BYPASS_CODE
    }


def _declared(pairing: Pairing) -> list[Mapping[str, Any]]:
    return [
        one
        for one in contract_of(pairing).get("workers") or []
        if isinstance(one, Mapping) and one.get("id")
    ]


def case_holds(pairing: Pairing, evidence: Evidence) -> Mapping[str, CaseHold] | None:
    """Each scenario's sealed hold by scenario token; None when the contract declares no workers, so no case relies
    on one. A bundle with no `worker-limits.json`, or one that could not be read, holds no entry: every case reads
    not known."""
    from simulate.services.hosted_harness_gateway import _scenario_token

    if not _declared(pairing):
        return None
    sealed = (evidence.worker_limits or {}).get("scenarios")
    if (
        evidence.unreadable
        or evidence.door_unreadable
        or not isinstance(sealed, Mapping)
    ):
        return {}
    holds: dict[str, CaseHold] = {}
    for key, entry in sealed.items():
        if not isinstance(entry, Mapping) or not isinstance(entry.get("held"), bool):
            continue
        # A deadline decided at run (the door inside the agent) is not held before the run.
        holds[_scenario_token(key)] = CaseHold(
            held=entry["held"],
            workers=tuple(sorted(str(one) for one in entry.get("workers") or [])),
            reason=(
                _CAUSE_REASONS.get(str(entry.get("cause")), _UNKNOWN_CAUSE)
                if entry["held"]
                else None
            ),
            relies=entry.get("relies_on_worker_limits") is True,
            unheld=(
                tuple(str(one) for one in entry["unheld_workers"])
                if isinstance(entry.get("unheld_workers"), list)
                else None
            ),
        )
    return holds


def case_hold(holds: Mapping[str, CaseHold] | None, key: str) -> CaseHold:
    """One case's hold; clear when no worker is declared, not known when ALK sealed no answer for it."""
    from simulate.services.hosted_harness_gateway import _scenario_token

    if holds is None:
        return NO_HOLD
    return holds.get(_scenario_token(key), _NOT_KNOWN)


def _routes(evidence: Evidence) -> dict[str, str]:
    """Each routed tool's sealed world action, from the tool proxy's sealed `actions.json` (action to tool)."""
    routes: dict[str, str] = {}
    for action, tool in sorted((evidence.actions_to_tools or {}).items()):
        if isinstance(tool, str) and tool:
            routes.setdefault(tool, str(action))
    return routes


def _limited(evidence: Evidence, worker_id: str) -> bool | None:
    """ALK's own `limited` flag for the worker from `worker-limits.json`; None when it sealed none."""
    sealed = ((evidence.worker_limits or {}).get("workers") or {}).get(worker_id)
    limited = sealed.get("limited") if isinstance(sealed, Mapping) else None
    return limited if isinstance(limited, bool) else None


def _unheld_status(limited: bool | None) -> Status:
    """A worker whose limits are not held: Blocked when ALK says it is limited, else Unavailable; not known reads
    Untested."""
    return "untested" if limited is None else "blocked" if limited else "unavailable"


def _bypasses(calls: list[CallExecution]) -> dict[str, list[WorkerBypass]]:
    """Each worker's recent calls that reached the world without its own channel, newest first."""
    found: dict[str, list[WorkerBypass]] = {}
    for call in calls:
        trial = (call.call_metadata or {}).get(TRIAL_STATE_KEY) or {}
        for notice in trial.get("notices") or []:
            if not isinstance(notice, Mapping) or notice.get("code") != BYPASS_CODE:
                continue
            for worker in notice.get("workers") or []:
                found.setdefault(str(worker), []).append(
                    {
                        "run_id": str(call.test_execution_id),
                        "call_execution_id": str(call.id),
                    }
                )
    return found


def recent_calls(runs: list[Any]) -> list[CallExecution]:
    """The newest calls of the agent's recent runs, read once for every worker's evidence."""
    if not runs:
        return []
    return list(
        CallExecution.objects.filter(
            test_execution_id__in=HostedHarnessJob.no_workspace_objects.filter(
                id__in=runs
            ).values("test_execution_id")
        ).order_by("-created_at")[:_CALLS_READ]
    )


def _rebuild(name: str, action: str) -> Fix:
    return {
        "type": "rebuild_environment",
        "category": "tool_routing",
        "target": None,
        "action": action,
        "would": f"{name}'s calls are held to its own tools",
        "recheck": None,
    }


def _no_door(reason: str) -> tuple[str, str]:
    """The code and reason for a build ALK sealed without a door (`door.json` `present: false`), per ALK's reason."""
    if reason == "tool_service_not_fronted":
        return (
            reason,
            "This agent's tool service does not run behind the world's door, so no worker's limits are held.",
        )
    if reason == "in_process_unbound":
        return (
            reason,
            "This agent's tools run inside it where no door can stand, so no worker's limits are held.",
        )
    return (
        "no_tool_service",
        "This agent has no tool service for the world's door to front, so no worker's limits are held.",
    )


def worker_boundaries(
    pairing: Pairing, evidence: Evidence, calls: list[CallExecution]
) -> list[WorkerBoundaryView]:
    """One row per declared worker, first matching rule wins, decided from the bundle root's `door.json`. No rule
    yields Supported."""
    declared = _declared(pairing)
    if not declared:
        return []
    routes = _routes(evidence)
    outcome = evidence.door_outcome
    bypasses = _bypasses(calls)
    permissions = evidence.worker_permissions or {}
    rows: list[WorkerBoundaryView] = []
    for worker in declared:
        worker_id = str(worker["id"])
        name = str(worker.get("name") or worker_id)
        binding: Literal["separate", "shared"] = (
            "separate" if worker.get("binding") == "separate" else "shared"
        )
        tools = [str(one) for one in worker.get("tools") or [] if one]
        sealed = permissions.get(f"worker:{worker_id}")
        allow = (
            set((sealed or {}).get("allow") or [])
            if isinstance(sealed, Mapping)
            else set()
        )
        limited = _limited(evidence, worker_id)
        status: Status
        held = False
        fix: Fix | None = None
        if evidence.unreadable or evidence.door_unreadable:
            status, code = "untested", "evidence_unreadable"
            reason = "This pairing's sealed files could not be read, so this worker's limits are not known."
        elif worker_id in bypasses:
            status, code, held = "blocked", BYPASS_CODE, isinstance(sealed, Mapping)
            reason = (
                f"{name} reached the world without its own channel in a recent run, so its"
                " limits were not held there."
            )
            fix = {
                "type": "rebind_worker",
                "category": "tool_routing",
                "target": worker_id,
                "action": (
                    f"Make {name}'s tool client use"
                    f" {worker.get('address_env') or 'its own address'} for every call"
                ),
                "would": "Its calls are held to its own tools again",
                "recheck": None,
            }
        elif outcome is None and evidence.worker_limits is not None:
            # Worker limits sealed without the door decision (an older compose build): a rebuild would not add it.
            status, code = "untested", "door_decision_not_sealed"
            reason = "This environment's build did not seal whether its world holds worker limits, so none are held."
        elif outcome is None:
            # Sealed before ALK wrote its door decision: whether a case relies on the worker is not known.
            status, code = "untested", "sealed_before_worker_limits"
            reason = (
                "This environment was sealed before worker limits, so none are held."
                " Rebuild the environment to hold them."
            )
            fix = _rebuild(
                name, "Rebuild the environment so its world holds worker limits"
            )
        elif not outcome.get("present"):
            # A rebuild cannot add a door to a build ALK found none could stand in front of.
            status = _unheld_status(limited)
            code, reason = _no_door(str(outcome.get("reason") or ""))
        elif outcome.get("surface") == "in_process" and worker_id not in {
            str(one) for one in outcome.get("workers") or []
        }:
            # The door inside the agent holds only the workers it lists; ALK holds this one's budgeted cases.
            status, code = _unheld_status(limited), "in_process_not_held"
            reason = "The world's door inside the agent does not hold this worker's deadline or its permitted tools."
        elif outcome.get("surface") == "in_process":
            status, code = "partial", "in_process_door"
            reason = (
                "The world's door inside the agent holds this worker's deadline when it runs as a task of its own; "
                "its permitted tools are not enforced, since its calls are not told apart."
            )
        elif binding == "shared":
            status, code = _unheld_status(limited), "shared_binding"
            reason = "Its calls reach the world without its name, so no limit is held for it."
            fix = _rebuild(
                name,
                f"Record {name} as separate in the contract, then rebuild the environment",
            )
        elif isinstance(sealed, Mapping):
            status, code, held = "partial", "held_at_world_cooperative", True
            reason = (
                "Held at the world on its own channel. Workers share the app's one process, so a call made through "
                "another address is not held; each such call is flagged."
            )
        else:
            status, code = _unheld_status(limited), "worker_not_sealed"
            reason = f"{name} is separate in the contract but has no sealed allow-list. Rebuild the environment."
            fix = _rebuild(
                name, "Rebuild the environment so its world holds worker limits"
            )
        views: list[WorkerToolView] = [
            {
                "tool": tool,
                "action": routes.get(tool),
                "boundary": (
                    "not_enforced"
                    if not held or tool not in routes
                    else "allowed"
                    if routes[tool] in allow
                    else "blocked"
                ),
            }
            for tool in tools
        ]
        bypassed = bypasses.get(worker_id, [])
        rows.append(
            {
                "worker_id": worker_id,
                "name": name,
                "binding": binding,
                "status": status,
                "status_label": _CODE_LABELS.get(code, _STATUS_LABELS[status]),
                "reason_code": code,
                "reason": reason,
                "tools": views,
                "bypassed": bypassed[:_BYPASSES_SHOWN],
                "bypassed_count": len(bypassed),
                "fix": fix,
                "checked_at": evidence.checked_at,
            }
        )
    return rows


def blocked_count(workers: list[WorkerBoundaryView]) -> int:
    """The declared workers read Blocked: what the run plan counts as blockers beside the rows."""
    return sum(1 for one in workers if one["status"] == "blocked")
