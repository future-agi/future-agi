"""What an agent and its environment support, as the user sees it: one status per capability, decided at read time
from the stored evidence and the current versions, so stale evidence can never read as current."""

from __future__ import annotations

from typing import Any

RULES_VERSION = "2"

SUPPORTED = "supported"
PARTIAL = "partial"
BLOCKED = "blocked"
UNAVAILABLE = "unavailable"
UNTESTED = "untested"
STALE = "stale"

_FINDING_STATUS = {
    "passed": SUPPORTED,
    "passed_with_limits": PARTIAL,
    "failed": BLOCKED,
    "unavailable": UNAVAILABLE,
}
_GATE_OPEN = {SUPPORTED, PARTIAL}


def _stale_because(inputs: dict[str, Any], current: dict[str, Any]) -> str | None:
    """Which input changed since the evidence was captured; None when every recorded input still matches."""
    changed = sorted(key for key, value in current.items() if key in inputs and inputs[key] != value)
    return ", ".join(changed) or None


def capability_statuses(
    catalogue: list[dict[str, Any]],
    evidence: dict[str, Any] | None,
    current_inputs: dict[str, Any],
    structural: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """One row per catalogue capability, in catalogue order.

    Rules, first match wins: a current finding (from the contract as it is now, or the agent's preflight) ->
    Blocked (it describes what exists now, so it outranks older evidence); inputs changed since capture -> Stale; a capability it depends on is
    not Supported or Partial -> Untested, naming the gate; then the finding: failed -> Blocked, unavailable ->
    Unavailable, passed_with_limits -> Partial, passed -> Supported; anything else, or no evidence -> Untested.
    """
    found = (evidence or {}).get("capabilities", {})
    structural = structural or {}
    stale_because = _stale_because((evidence or {}).get("inputs", {}), current_inputs) if evidence else None
    status_by_id: dict[str, str] = {}
    rows = []
    for entry in catalogue:
        key = entry["id"]
        reading = structural.get(key) or found.get(key, {})
        finding = reading.get("finding")
        gates = [gate for gate in entry.get("depends_on", []) if gate in status_by_id]
        closed = [gate for gate in gates if status_by_id[gate] not in _GATE_OPEN]
        if key in structural:
            status = BLOCKED
        elif stale_because:
            status = STALE
        elif closed:
            status = UNTESTED
        else:
            status = _FINDING_STATUS.get(finding or "", UNTESTED)
        status_by_id[key] = status
        rows.append(
            {
                "id": key,
                "label": entry.get("label", key),
                "status": status,
                "finding": finding,
                "checks": reading.get("checks", []),
                "blocked_by": closed[0] if status == UNTESTED and closed else None,
                "stale_because": stale_because if status == STALE else None,
                "reason_code": reading.get("reason_code") if status == BLOCKED else None,
                "reason": reading.get("reason") if status == BLOCKED else None,
                "fix": reading.get("fix") if status == BLOCKED else None,
                "fix_type": entry.get("fix_type"),
            }
        )
    return rows
