"""The rules that turn readings into row statuses and fixes, first match wins."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from simulate.models import HarnessAgentVersionRuntime, HarnessAssembledBundle
from simulate.services.harness_readiness.catalogue import (
    CATALOGUE,
    GATE_OPEN,
    WORST_FIRST,
    AnswerView,
    Capability,
    Fix,
    FixRecheck,
    Pairing,
    Reading,
    RowView,
    Status,
    targets,
)
from simulate.services.harness_readiness.evidence import Evidence, readings
from simulate.services.harness_readiness.findings import (
    preflight_findings,
    structural_findings,
)
from simulate.services.harness_versioning import assembly

_BASE_PATH = "/simulate/api/harness-environments"


def _status(reading: Reading) -> tuple[Status, str | None, str | None]:
    """Rules 5 to 9: a measured row's status from its reading alone."""
    if reading.finding == "failed":
        return "blocked", reading.reason_code, reading.reason
    if reading.finding == "not_applicable":
        return (
            "unavailable",
            "not_applicable",
            "This agent's lane cannot have this check",
        )
    if reading.finding == "limited":
        return "partial", reading.reason_code, reading.reason
    if reading.finding == "passed":
        return "supported", None, None
    return "untested", "not_run", "Not checked yet"


def _row(
    capability: Capability,
    status: Status,
    *,
    reading: Reading | None = None,
    reason_code: str | None = None,
    reason: str | None = None,
    checked_at: str | None = None,
    blocked_by: str | None = None,
    stale_because: str | None = None,
    was: Status | None = None,
) -> RowView:
    return {
        "id": capability.id,
        "label": capability.label,
        "area": capability.area,
        "status": status,
        "measured": capability.measured,
        "finding": reading.finding if reading is not None else None,
        "reason_code": reason_code,
        "reason": reason,
        "targets": list(reading.targets) if reading is not None else [],
        "blocked_by": blocked_by,
        "stale_because": stale_because,
        "was": was,
        "evidence": list(reading.evidence) if reading is not None else [],
        "checked_at": checked_at if reading is not None else None,
        "fix": None,
    }


def rows(
    pairing: Pairing,
    evidence: Evidence,
    bundle: HarnessAssembledBundle | None,
) -> list[RowView]:
    """One row per capability, first matching rule wins.

    Unmeasured rows are Untested. ``start`` is Blocked on a failed or unfinished
    preflight. Then: (1) a failed pair blocks tool routing on the actions it failed;
    (3) evidence measured with another agent is Stale; (4) a gate not Supported or
    Partial leaves the row Untested; (5-9) the row's own reading.
    """
    measured = _measured(pairing, evidence)
    preflight = preflight_findings(pairing.runtime)
    named = failed_actions(bundle)
    stale = _stale(pairing, evidence)
    rows: list[RowView] = []
    statuses: dict[str, Status] = {}
    fresh: dict[str, Status] = {}
    for capability in CATALOGUE:
        reading = measured.get(capability.id)
        if not capability.measured or reading is None:
            row = _row(
                capability,
                "untested",
                reason_code="no_check_yet",
                reason="No check measures this yet",
            )
        elif capability.id == "start" and preflight is not None:
            row = _row(
                capability,
                "blocked",
                reading=preflight,
                reason_code=preflight.reason_code,
                reason=preflight.reason,
                checked_at=pairing.runtime.updated_at.isoformat(),
            )
        elif capability.id == "tool_routing" and named:
            names = [one["action"] for one in named]
            failed = Reading(
                "failed",
                "action_failed",
                "Failed its probe: " + ", ".join(names),
                targets("action", names, "failed"),
                reading.evidence,
            )
            row = _row(
                capability,
                "blocked",
                reading=failed,
                reason_code=failed.reason_code,
                reason=failed.reason,
                checked_at=bundle.updated_at.isoformat(),
            )
        else:
            fresh[capability.id] = _gated(capability, reading, fresh)[0]
            if stale == "built_with_unknown":
                row = _row(
                    capability,
                    "untested",
                    reading=reading,
                    reason_code="built_with_unknown",
                    reason="The agent this environment was built with is not known",
                    checked_at=evidence.checked_at,
                )
            elif stale == "agent_version":
                row = _row(
                    capability,
                    "stale",
                    reading=reading,
                    reason_code="agent_changed",
                    reason="Checked with the agent version the environment was built with",
                    checked_at=evidence.checked_at,
                    stale_because="agent_version",
                    was=fresh[capability.id],
                )
            else:
                status, code, reason, gate = _gated(capability, reading, statuses)
                row = _row(
                    capability,
                    status,
                    reading=reading,
                    reason_code=code,
                    reason=reason,
                    checked_at=evidence.checked_at,
                    blocked_by=gate,
                )
        statuses[capability.id] = row["status"]
        fresh.setdefault(capability.id, row["status"])
        row["fix"] = _fix(pairing, row)
        rows.append(row)
    return rows


def _gated(
    capability: Capability, reading: Reading, statuses: Mapping[str, Status]
) -> tuple[Status, str | None, str | None, str | None]:
    gate = capability.gate
    if gate is not None and statuses.get(gate) not in GATE_OPEN:
        return "untested", "blocked_by", f"Waits on {gate}", gate
    return (*_status(reading), None)


def failed_actions(bundle: HarnessAssembledBundle | None) -> list[Mapping[str, Any]]:
    if bundle is None or bundle.state != HarnessAssembledBundle.State.FAILED:
        return []
    return [
        one
        for one in (bundle.error or {}).get("failed_actions") or []
        if isinstance(one, Mapping) and one.get("action")
    ]


def _stale(
    pairing: Pairing, evidence: Evidence
) -> Literal["agent_version", "built_with_unknown"] | None:
    """Rule 3: evidence other than the pairing's own was measured with the agent the
    environment version was built with."""
    if evidence.source == "pair_certificate":
        return None
    built_with = pairing.environment_version.agent_version_id
    if built_with is None:
        return "built_with_unknown"
    picked = pairing.agent_version.id if pairing.agent_version is not None else None
    if assembly.same_bundle_agent(picked, built_with, pairing.environment):
        return None
    return "agent_version"


# --- fixes ------------------------------------------------------------------------------

_FIXES: dict[str, tuple[str, str]] = {
    "start": (
        "Make the agent start in the test sandbox",
        "The agent starts and its start checks pass",
    ),
    "message": (
        "Map the agent's chat input so a test message reaches it",
        "A test message reaches the agent and it answers",
    ),
    "tool_discovery": (
        "Expose {target} so the harness can find and call it",
        "{target} is discovered before calls",
    ),
    "tool_routing": (
        "Point {target} at the test world",
        "{target} lands in the test world and its probe passes",
    ),
    "state": (
        "Give the environment a copy of the data the agent reads",
        "The world is seeded and its state check passes",
    ),
    "reset": (
        "Make the world return to its seeded state between cases",
        "The world resets and its reset check passes",
    ),
}


def _fix(pairing: Pairing, row: RowView) -> Fix | None:
    """What to change for a measured row that is Blocked, Stale or Untested, and the
    re-check that reads it again."""
    if not row["measured"] or row["status"] not in ("blocked", "stale", "untested"):
        return None
    environment_id = pairing.environment.id
    pair: FixRecheck = {
        "scope": "pair",
        "method": "POST",
        "path": f"{_BASE_PATH}/{environment_id}/capabilities/recheck/",
    }
    code = row["reason_code"]
    if code in ("preflight_failed", "preflight_pending"):
        return {
            "type": "preflight",
            "target": None,
            "action": _preflight_action(pairing.runtime),
            "would": "The agent passes its preflight and can start",
            "recheck": {
                "scope": "agent",
                "method": "POST",
                "path": (
                    f"{_BASE_PATH}/{environment_id}/versions/agent/"
                    f"{pairing.agent_version.id}/retry/"
                ),
            },
        }
    if row["status"] == "stale" or code in ("built_with_unknown", "not_run"):
        return {
            "type": "recheck",
            "target": None,
            "action": "Check this agent with this environment and suite",
            "would": "The row reads what this agent does",
            "recheck": pair,
        }
    if row["status"] == "untested":
        gate = row["blocked_by"]
        labels = {one.id: one.label for one in CATALOGUE}
        return {
            "type": "gate",
            "target": gate,
            "action": f"Fix {labels.get(gate, gate)} first",
            "would": f"{row['label']} is checked once {labels.get(gate, gate)} is Supported",
            "recheck": pair,
        }
    target = row["targets"][0]["name"] if row["targets"] else None
    action, would = _FIXES[row["id"]]
    subject = target or "the failing tools"
    return {
        "type": row["id"],
        "target": target,
        "action": action.format(target=subject),
        "would": would.format(target=subject),
        "recheck": pair,
    }


def _preflight_action(runtime: HarnessAgentVersionRuntime | None) -> str:
    report = (runtime.preflight_report if runtime is not None else None) or {}
    failed = [
        check
        for check in report.get("checks") or []
        if isinstance(check, Mapping) and check.get("status") == "failed"
    ]
    if not failed:
        return "Wait for the agent's preflight, or check the agent again"
    parts = []
    for check in failed:
        missing = [str(name) for name in check.get("missing") or []]
        parts.append(
            f"{check.get('id')}: provide {', '.join(missing)}"
            if missing
            else f"{check.get('id')}: fix it in the agent's connection"
        )
    return "Fix the agent's preflight checks (" + "; ".join(parts) + ")"


def worst(statuses: Iterable[Status]) -> Status:
    found = set(statuses)
    return next(status for status in WORST_FIRST if status in found)


def answer(
    rows: list[RowView], *, validating: bool, failed: bool, named: bool
) -> AnswerView:
    """Read from the measured rows: unmeasured ones hold no case back. A failed pair
    whose failure names no action blocks the answer without blocking a row."""
    lowest = worst(row["status"] for row in rows if row["measured"])
    pair_failed = failed and not named
    if validating:
        kind = "validating"
    elif lowest == "blocked" or pair_failed:
        kind = "blocked"
    elif lowest == "stale":
        kind = "stale"
    elif lowest == "untested":
        kind = "unchecked"
    else:
        kind = "ready"
    return {
        "kind": kind,
        "worst": lowest,
        "reason_code": "pair_failed" if pair_failed else None,
    }


def _measured(pairing: Pairing, evidence: Evidence) -> dict[str, Reading]:
    """Each measured row's reading; the contract's finding outranks the certificate's
    on tool routing."""
    found = readings(pairing, evidence)
    contract = (pairing.environment_version.documents or {}).get("contract")
    structural = structural_findings(
        contract if isinstance(contract, Mapping) else None
    )
    if structural is not None:
        found["tool_routing"] = Reading(
            "failed",
            structural.reason_code,
            structural.reason,
            structural.targets,
            found["tool_routing"].evidence,
        )
    return found
