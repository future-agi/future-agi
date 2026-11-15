"""Run readiness, per declared worker: which of its tools the world's door holds it to, read from the sealed
`door.json` and `permissions.json`, the contract's wiring and the agent's own runs.

The best a worker reads is Partial: workers share the app's one process, so the limit is held at the world on the
worker's own channel only, and a call made through another address is not held (each such call is flagged).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from simulate.models import CallExecution, HostedHarnessJob
from simulate.services.harness_readiness.catalogue import (
    Fix,
    Pairing,
    WorkerBoundaryView,
    WorkerBypass,
    WorkerToolView,
)
from simulate.services.harness_readiness.evidence import Evidence

BYPASS_CODE = "worker_channel_bypassed"
_BYPASSES_SHOWN = 5
_CALLS_READ = 50


def _world_action(route: str) -> str:
    """ALK's `world_door.world_action`: the path without its query, `/` read as `.`."""
    return str(route or "").split("?", 1)[0].strip("/").replace("/", ".")


def _method_action(method: str, action: str) -> str:
    """ALK's `world_door.method_action`: a missing method is POST."""
    return f"{(method or 'POST').upper()} {action}"


def _contract(pairing: Pairing) -> Mapping[str, Any]:
    contract = (pairing.environment_version.documents or {}).get("contract")
    return contract if isinstance(contract, Mapping) else {}


def _routes(contract: Mapping[str, Any]) -> dict[str, str]:
    """Each routed tool's sealed world action, as ALK seals it."""
    routes: dict[str, str] = {}
    for entry in contract.get("tool_entrypoints") or []:
        if not isinstance(entry, Mapping):
            continue
        tool, endpoint = str(entry.get("tool") or ""), str(entry.get("endpoint") or "")
        if tool and endpoint.strip():
            routes[tool] = _method_action(
                str(entry.get("method") or ""), _world_action(endpoint)
            )
    return routes


def _bypasses(pairing: Pairing, runs: list[Any]) -> dict[str, list[WorkerBypass]]:
    """Each worker's recent calls that reached the world without its own channel, newest first."""
    from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY

    if not runs:
        return {}
    calls = CallExecution.objects.filter(
        test_execution_id__in=HostedHarnessJob.no_workspace_objects.filter(
            id__in=runs
        ).values("test_execution_id")
    ).order_by("-created_at")[:_CALLS_READ]
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
                        "tool": None,
                    }
                )
    return {worker: listed[:_BYPASSES_SHOWN] for worker, listed in found.items()}


def _rebuild(
    name: str, action: str = "Rebuild the environment so its world holds worker limits"
) -> Fix:
    return {
        "type": "rebuild_environment",
        "category": "tool_routing",
        "target": None,
        "action": action,
        "would": f"{name}'s calls are held to its own tools",
        "recheck": None,
    }


# ALK puts the world's door in front of the agent's tool service only when it runs as this service.
_DOOR_SERVICE = "tools-api"


def _behind_door_service(contract: Mapping[str, Any]) -> bool:
    """Whether the contract reaches its tools on the service the door fronts: a seal without a door then predates
    worker limits; otherwise the build never had a door."""
    return any(
        isinstance(entry, Mapping)
        and entry.get("mode") == "service"
        and entry.get("service") == _DOOR_SERVICE
        for entry in contract.get("tool_entrypoints") or []
    )


def worker_boundaries(
    pairing: Pairing, evidence: Evidence, runs: list[Any]
) -> list[WorkerBoundaryView]:
    """One row per declared worker, first matching rule wins. No rule yields Supported."""
    contract = _contract(pairing)
    declared = [
        one
        for one in contract.get("workers") or []
        if isinstance(one, Mapping) and one.get("id")
    ]
    if not declared:
        return []
    routes = _routes(contract)
    bypasses = _bypasses(pairing, runs)
    door = evidence.door
    permissions = evidence.worker_permissions or {}
    rows: list[WorkerBoundaryView] = []
    for worker in declared:
        worker_id = str(worker["id"])
        name = str(worker.get("name") or worker_id)
        binding = "separate" if worker.get("binding") == "separate" else "shared"
        tools = [str(one) for one in worker.get("tools") or [] if one]
        sealed = permissions.get(f"worker:{worker_id}")
        allow = (
            set((sealed or {}).get("allow") or [])
            if isinstance(sealed, Mapping)
            else set()
        )

        def views(held: bool, tools=tools, allow=allow) -> list[WorkerToolView]:
            return [
                {
                    "tool": tool,
                    "action": routes.get(tool),
                    "boundary": (
                        "not_enforced"
                        if not held or routes.get(tool) is None
                        else "allowed"
                        if routes[tool] in allow
                        else "blocked"
                    ),
                }
                for tool in tools
            ]

        held_on_channel = binding == "separate" and isinstance(sealed, Mapping)
        # A worker held to its own tools, or wired apart so a case can give it a deadline: the harness leaves a case
        # that checks such a worker ungraded where nothing holds its limits, so that case is held back here.
        limited = bool(worker.get("allowed_tools")) or binding == "separate"
        status, code, reason, fix, held = (
            "blocked",
            "worker_not_sealed",
            "",
            None,
            False,
        )
        if evidence.unreadable:
            status, code = "untested", "evidence_unreadable"
            reason = "This pairing's sealed files could not be read, so this worker's limits are not known."
        elif worker_id in bypasses:
            status, code, held = "blocked", "worker_channel_bypassed", held_on_channel
            reason = f"{name} reached the world without its own channel in a recent run, so its limits were not held there."
            fix = {
                "type": "rebind_worker",
                "category": "tool_routing",
                "target": worker_id,
                "action": f"Make {name}'s tool client use {worker.get('address_env') or 'its own address'} for every call",
                "would": "Its calls are held to its own tools again",
                "recheck": None,
            }
        elif not routes:
            status = "blocked" if limited else "unavailable"
            code = "in_process_tools"
            reason = (
                "This agent's tools run inside its own process, not through the world's door, so no worker's limits "
                "are held."
            )
            fix = (
                _rebuild(
                    name,
                    "Serve the agent's tools as a service behind the world's door, then rebuild",
                )
                if status == "blocked"
                else None
            )
        elif door is None and not _behind_door_service(contract):
            status = "blocked" if limited else "unavailable"
            code = "no_world_door"
            reason = (
                "This build has no world door: its tools reach their service directly, so no worker's limits are "
                "held. Rebuild with the tool service behind the door to hold them."
            )
            fix = (
                _rebuild(
                    name,
                    "Rebuild the environment with its tool service behind the world's door",
                )
                if status == "blocked"
                else None
            )
        elif door is None or door.get("worker_deadlines") != "per_worker":
            status = "blocked" if limited else "unavailable"
            code = "sealed_before_worker_limits"
            reason = "This environment was sealed before worker limits, so none are held. Rebuild the environment to hold them."
            fix = _rebuild(name) if status == "blocked" else None
        elif held_on_channel:
            status, code, held = "partial", "held_at_world_cooperative", True
            reason = (
                "Held at the world on its own channel. Workers share the app's one process, so a call made through "
                "another address is not held; each such call is flagged."
            )
        elif binding == "shared":
            status, code = "unavailable", "shared_binding"
            reason = "Its calls reach the world without its name, so no limit is held for it."
        else:
            reason = f"{name} is separate in the contract but has no sealed allow-list. Rebuild the environment."
            fix = _rebuild(name)
        rows.append(
            {
                "worker_id": worker_id,
                "name": name,
                "binding": binding,
                "status": status,
                "reason_code": code,
                "reason": reason,
                "tools": views(held),
                "bypassed": bypasses.get(worker_id, []),
                "fix": fix,
                "checked_at": evidence.checked_at,
            }
        )
    return rows


def blocked_workers(boundaries: list[WorkerBoundaryView]) -> frozenset[str]:
    """The workers whose limits a run cannot rely on: cases relying on them are held."""
    return frozenset(
        one["worker_id"] for one in boundaries if one["status"] == "blocked"
    )
