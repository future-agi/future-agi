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
    FixCategory,
    FixRecheck,
    Pairing,
    Reading,
    RowView,
    Status,
    targets,
)
from simulate.services.harness_readiness.evidence import (
    Evidence,
    connection,
    observed,
    readings,
)
from simulate.services.harness_readiness.findings import (
    agent_findings,
    preflight_findings,
    structural_findings,
)
from simulate.services.harness_versioning import assembly
from simulate.services.harness_versioning.read import _label as version_label

_BASE_PATH = "/simulate/api/harness-environments"


def _status(reading: Reading) -> tuple[Status, str | None, str | None]:
    """Rules 5 to 9: a measured row's status from its reading alone."""
    if reading.finding == "failed":
        return "blocked", reading.reason_code, reading.reason
    if reading.finding == "not_applicable":
        return (
            "unavailable",
            reading.reason_code or "not_applicable",
            reading.reason or "This agent's lane cannot have this check",
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
        "consequence": "",
    }


def rows(
    pairing: Pairing,
    evidence: Evidence,
    bundle: HarnessAssembledBundle | None,
    *,
    agent_tools: list[Mapping[str, Any]] | None = None,
) -> list[RowView]:
    """One row per capability, first matching rule wins.

    Unmeasured rows are Untested. ``start`` is Blocked on a failed or unfinished
    preflight. Then: (0) the agent version's own tools against the environment's,
    for an agent other than the one it was built with, whatever the certificate
    says; (1) a failed pair blocks tool routing on the actions it failed; (3)
    evidence measured with another agent is Stale; (4) a gate not Supported or
    Partial leaves the row Untested; (5-9) the row's own reading.
    """
    measured = _measured(pairing, evidence)
    excerpt = _failed_check_excerpt(bundle)
    if excerpt and "start" in measured:
        start = measured["start"]
        measured["start"] = Reading(
            start.finding,
            start.reason_code,
            start.reason,
            start.targets,
            [*start.evidence, {"check": "failed_check", "result": excerpt}],
            start.checked_at,
        )
    preflight = preflight_findings(pairing.runtime)
    named = failed_actions(bundle)
    stale = _stale(pairing, evidence)
    contract = (pairing.environment_version.documents or {}).get("contract")
    structural = structural_findings(
        contract if isinstance(contract, Mapping) else None
    )
    gap = _agent_gap(pairing, agent_tools)
    rows: list[RowView] = []
    statuses: dict[str, Status] = {}
    fresh: dict[str, Status] = {}
    seen = observed(pairing)
    for capability in CATALOGUE:
        reading = measured.get(capability.id)
        if not capability.measured and capability.id in seen:
            # Read from the agent's runs: what they show, never gated or Stale.
            found = seen[capability.id]
            status, _, _ = _status(found)
            row = _row(
                capability,
                status,
                reading=found,
                reason_code=found.reason_code,
                reason=found.reason,
                checked_at=found.checked_at,
            )
        elif not capability.measured or reading is None:
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
        elif capability.id == "cancel":
            # Read from the agent's runs, not the certificate: never Stale or gated.
            status, code, reason = _status(reading)
            if reading.finding == "not_run":
                code, reason = reading.reason_code, reading.reason
            row = _row(
                capability,
                status,
                reading=reading,
                reason_code=code,
                reason=reason,
                checked_at=reading.checked_at,
            )
        elif capability.id in gap:
            found = _with_measured(gap[capability.id], reading)
            if capability.id == "tool_routing" and named:
                names = [one["action"] for one in named]
                found = Reading(
                    found.finding,
                    found.reason_code,
                    found.reason + "; failed its probe: " + ", ".join(names),
                    found.targets + targets("action", names, "failed"),
                )
            status, _, _ = _status(found)
            row = _row(
                capability,
                status,
                reading=found,
                reason_code=found.reason_code,
                reason=found.reason,
            )
            # The measured reading still holds the cases the gap's tools leave out.
            if stale is not None:
                row["stale_because"] = stale
                if stale != "built_with_unknown":
                    row["was"] = _gated(capability, reading, fresh)[0]
            else:
                row["blocked_by"] = _gated(capability, reading, statuses)[3]
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
            elif stale is not None:
                code, reason = _stale_reason(stale, pairing, evidence)
                row = _row(
                    capability,
                    "stale",
                    reading=reading,
                    reason_code=code,
                    reason=reason,
                    checked_at=evidence.checked_at,
                    stale_because=stale,
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
        if capability.id == "tool_routing" and structural is not None:
            # The contract's finding is a fact of the environment version: no other
            # reading (another agent's tools, a stale certificate) can drop it.
            _keep_structural(row, structural)
        statuses[capability.id] = row["status"]
        fresh.setdefault(capability.id, row["status"])
        row["fix"] = _fix(pairing, row, certified=evidence.source == "pair_certificate")
        row["consequence"] = consequence(row, connection(pairing.runtime))
        rows.append(row)
    return rows


def _keep_structural(row: RowView, structural: Any) -> None:
    """Make tool routing Blocked on the contract's finding, keeping what the row's own
    reading adds (another agent's tool gaps)."""
    if row["status"] == "blocked" and row["reason_code"] == structural.reason_code:
        return
    present = list(row["targets"])
    row["targets"] = present + [one for one in structural.targets if one not in present]
    if row["status"] == "blocked":
        row["reason"] = (
            f"{structural.reason}; {row['reason']}"
            if row["reason"]
            else structural.reason
        )
    else:
        row["reason"] = structural.reason
        row["reason_code"] = structural.reason_code
        row["finding"] = "failed"
        row["stale_because"] = None
        row["was"] = None
        row["blocked_by"] = None
    row["status"] = "blocked"


def _agent_gap(
    pairing: Pairing, agent_tools: list[Mapping[str, Any]] | None
) -> dict[str, Reading]:
    """The agent-side tool rows of a pairing whose agent is not the one its
    environment version was built with (that one's tools are the contract's)."""
    if pairing.agent_version is None or assembly.same_bundle_agent(
        pairing.agent_version.id,
        pairing.environment_version.agent_version_id,
        pairing.environment,
    ):
        return {}
    contract = (pairing.environment_version.documents or {}).get("contract")
    return agent_findings(
        agent_tools, contract if isinstance(contract, Mapping) else None
    )


def _with_measured(found: Reading, measured: Reading) -> Reading:
    """The agent-side gap with the row's own measured failure kept beside it: a tool
    the evidence failed stays failed whatever the agent version changed. A failure
    naming no tool, on either side, holds every case."""
    if measured.finding != "failed":
        return found
    reason = f"{found.reason}; {measured.reason}"
    if found.finding != "failed":
        return Reading(found.finding, found.reason_code, reason, [], measured.evidence)
    named = found.targets and measured.targets
    return Reading(
        "failed",
        found.reason_code,
        reason,
        found.targets + measured.targets if named else [],
        measured.evidence,
    )


def _gated(
    capability: Capability, reading: Reading, statuses: Mapping[str, Status]
) -> tuple[Status, str | None, str | None, str | None]:
    gate = capability.gate
    if gate is not None and statuses.get(gate) not in GATE_OPEN:
        if statuses.get(gate) == "unavailable" and reading.finding == "not_applicable":
            # Neither exists for this connection: nothing waits on the gate.
            return (*_status(reading), None)
        return "untested", "blocked_by", f"Waits on {gate}", gate
    return (*_status(reading), None)


def _failed_check_excerpt(bundle: HarnessAssembledBundle | None) -> str | None:
    """What the sandbox's failure record reported for the check this pairing failed, labelled as reported."""
    if bundle is None or bundle.state != HarnessAssembledBundle.State.FAILED:
        return None
    excerpt = (bundle.error or {}).get("excerpt")
    return str(excerpt) if excerpt else None


def failed_actions(bundle: HarnessAssembledBundle | None) -> list[Mapping[str, Any]]:
    if bundle is None or bundle.state != HarnessAssembledBundle.State.FAILED:
        return []
    return [
        one
        for one in (bundle.error or {}).get("failed_actions") or []
        if isinstance(one, Mapping) and one.get("action")
    ]


StaleBecause = Literal["agent_version", "environment_version", "suite_version"]


def _stale(
    pairing: Pairing, evidence: Evidence
) -> StaleBecause | Literal["built_with_unknown"] | None:
    """Rule 3: evidence other than the pairing's own was measured with the agent the
    environment version was built with, on the environment version that holds the
    build certificate (an edit saves a new one without certifying it) and on the
    scenarios that build made."""
    if evidence.source == "pair_certificate":
        return None
    built_with = pairing.environment_version.agent_version_id
    if built_with is None:
        return "built_with_unknown"
    picked = pairing.agent_version.id if pairing.agent_version is not None else None
    if not assembly.same_bundle_agent(picked, built_with, pairing.environment):
        return "agent_version"
    if evidence.made_for is not None:
        return "environment_version"
    if evidence.covers_suite is False:
        return "suite_version"
    return None


def _stale_reason(
    stale: StaleBecause, pairing: Pairing, evidence: Evidence
) -> tuple[str, str]:
    if stale == "agent_version":
        return (
            "agent_changed",
            "Checked with the agent version the environment was built with",
        )
    now = _label(pairing.environment_version.version_number)
    if stale == "environment_version":
        was = _label(evidence.made_for.version_number)
        return (
            "environment_changed",
            f"Checked with environment version {was}; this pairing uses {now}",
        )
    return (
        "suite_changed",
        f"Checked with the scenarios environment version {now} was built with; this"
        f" pairing uses suite version {_label(pairing.suite_version.version_number)}",
    )


def _label(number: int | None) -> str:
    return version_label(number) or "a draft"


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

# The agent version's tools against the environment version's.
_AGENT_GAP_FIXES: dict[str, tuple[str, str]] = {
    "tool_not_in_world": (
        "Add {target} to this environment in the builder, or test this agent version"
        " in an environment that has it",
        "{target} is simulated in the test world and its probe passes",
    ),
    "tool_not_in_agent": (
        "Give this agent version {target}, or pick an agent version that has it",
        "The agent can call {target} in the cases that use it",
    ),
    "tool_signature_changed": (
        "Rebuild this environment from this agent version so {target} takes the"
        " arguments the agent sends, or test it in an environment built for them",
        "{target} takes the agent's arguments in the test world and its probe passes",
    ),
}

# Each fixable row's kind of repair: the screen its fix opens.
_CATEGORIES: dict[str, FixCategory] = {
    "start": "startup",
    "message": "message_mapping",
    "tool_discovery": "tool_routing",
    "tool_routing": "tool_routing",
    "state": "evidence",
    "reset": "evidence",
}

# What a row's status means for the cases, by row then ``status:reason_code`` or
# ``status``; ``{target}`` is the row's first target. A status a row does not name reads
# the shared line below.
_CONSEQUENCES: dict[str, dict[str, str]] = {
    "start": {
        "supported": "The agent starts in the test sandbox, so every case can begin",
        "blocked": "No case can run until the agent starts",
    },
    "message": {
        "supported": "Test messages reach the agent and it answers",
        "blocked": "Chat cases cannot run until a test message reaches the agent",
        "unavailable": "This connection takes no chat message; only voice cases apply",
    },
    "tool_discovery": {
        "blocked:tool_not_in_world": "In a run, {target} would reach outside the test"
        " world or fail; its effect would not be graded, so no case can run",
        "blocked:tool_not_in_agent": "Cases that use {target} cannot run: this agent"
        " version does not have it",
        "untested:agent_tools_unread": "This agent version's tools are not known, so a"
        " tool the environment lacks would go unseen until a run calls it",
        "supported": "Every tool the cases use is found before they call it",
        "partial": "Some tools appear only once the agent runs, so a case can fail on a"
        " tool found late",
        "blocked": "Cases that use {target} cannot run until it is exposed",
    },
    "tool_routing": {
        "blocked:tool_signature_changed": "Cases that use {target} cannot run: the"
        " agent calls it with other arguments than the test world takes",
        "blocked:tool_not_routed": "Cases that use {target} cannot run: the agent's"
        " call to it never reached the test world, so it may reach a real system",
        "supported": "Every tool call lands in the test world, never in a real system",
        "partial": "Some tools were not shown reaching the test world, so a case may"
        " still reach a real system through them",
        "unavailable": "The agent's tools run outside the test world, so their effects"
        " are not graded from it",
        "blocked": "Cases that use {target} cannot run until it is routed to the test"
        " world",
    },
    "state": {
        "supported": "Every case starts from the seeded test data",
        "blocked": "Cases that read the world's data cannot run until it is seeded",
        "unavailable": "This connection keeps its own data; there is no seeded world of"
        " ours",
    },
    "reset": {
        "supported": "Every trial starts from the same seed, agent included",
        "partial": "The world resets between trials; the agent may carry its own memory",
        "partial:isolation_not_run": "The world resets between trials, but cases run at"
        " the same time were not shown to stay apart",
        "blocked": "Trials can see each other's changes, so repeated results are not"
        " independent",
        "unavailable": "No seeded world of ours to reset for this connection",
    },
}
_SHARED_CONSEQUENCES: dict[Status, str] = {
    "supported": "Shown to work for this pairing",
    "partial": "Works within the limits the reason names",
    "blocked": "Cases that need this cannot run until it is fixed",
    "unavailable": "This connection does not offer this",
    "untested": "Not shown to work yet; a case that needs it can fail for this, not"
    " for the agent",
    "stale": "Checked with another agent version; check again before trusting cases"
    " that need this",
}
# What a Stop ends, by where the agent runs, until a Stop is confirmed.
_CANCEL_BY_CONNECTION = {
    "sandbox": "Stopping ends the agent with its test sandbox; this is confirmed once a"
    " run of this agent is stopped",
    "remote": "Stopping ends our side; the agent may keep running on its own side",
    "provider": "Stopping ends our side of the call; the provider may keep the call"
    " open and charge for it",
}
_CANCEL_CONFIRMED = (
    "A Stop ends the agent with its test sandbox, so a stopped run spends nothing more"
)
_UNMEASURED = (
    "No check measures this yet; results that depend on it are not verified either way"
)
# Rows read from the agent's runs: what each status means for the cases. They hold no
# case back either way.
_OBSERVED: dict[str, dict[str, str]] = {
    "continuity": {
        "partial": "Runs went past one turn; a case that depends on remembering an"
        " earlier turn is not verified for it",
        "untested": "No run has gone past one turn yet; multi-turn results are not"
        " verified for it",
    },
    "streaming": {
        "unavailable:whole_replies": "Each reply is read whole, so a case never ends"
        " on an open stream",
        "unavailable:voice_call": "Voice cases are heard over the call; no chat stream"
        " applies",
        "unavailable:transport_not_qualified": "This transport is not supported for"
        " runs until it passes qualification",
        "untested": "How this connection replies is not recorded, so where a case ends"
        " is not verified",
    },
    "audio": {
        "supported": "Calls of this agent were recorded, so audio checks have evidence",
        "unavailable": "Audio checks do not apply to a chat connection",
        "untested": "No call of this agent was recorded yet; audio checks have no"
        " evidence until one is",
    },
    "files": {
        "untested": "No run records an agent's files yet; file checks have no evidence",
    },
    "resume": {
        "supported": "An interrupted run finished on a new attempt, keeping the cases"
        " it had finished",
        "untested": "No interrupted run of this agent has been resumed yet",
    },
    "cost": {
        "partial": "Shown where measured: our side of a run, never the agent's own model"
        " spend",
        "untested": "Nothing is shown until a run is measured; no cost is estimated",
    },
}


def consequence(row: RowView, where: str | None = None) -> str:
    """What ``row``'s status means for the cases, in plain words; ``where`` is the
    agent's connection, which decides where it starts and what a Stop can end."""
    if not row["measured"]:
        own = _OBSERVED.get(row["id"], {})
        return (
            own.get(f"{row['status']}:{row['reason_code']}")
            or own.get(row["status"])
            or _UNMEASURED
        )
    if (
        row["id"] == "start"
        and row["status"] == "supported"
        and where in ("remote", "provider")
    ):
        return "The agent runs on its own side and its start checks passed, so every case can begin"
    if row["id"] == "cancel":
        if row["status"] == "supported":
            return _CANCEL_CONFIRMED
        return _CANCEL_BY_CONNECTION.get(where or "", _CANCEL_BY_CONNECTION["provider"])
    own = _CONSEQUENCES.get(row["id"], {})
    status = row["status"]
    text = (
        own.get(f"{status}:{row['reason_code']}")
        or own.get(status)
        or _SHARED_CONSEQUENCES[status]
    )
    target = row["targets"][0]["name"] if row["targets"] else "the failing tools"
    return text.format(target=target)


def _fix(pairing: Pairing, row: RowView, *, certified: bool) -> Fix | None:
    """What to change for a measured row that is Blocked, Stale or Untested, and the
    re-check that reads it again.

    A pairing ``certified`` already has its own evidence: checking it again starts
    nothing and changes no row, so its fixes offer no pair re-check, and a check that
    did not run there is fixed like a failed one.
    """
    if not row["measured"] or row["status"] not in ("blocked", "stale", "untested"):
        return None
    if row["id"] == "cancel":
        # Proved by stopping a run, never by checking the pairing again.
        return None
    environment_id = pairing.environment.id
    pair: FixRecheck | None = (
        None
        if certified
        else {
            "scope": "pair",
            "method": "POST",
            "path": f"{_BASE_PATH}/{environment_id}/capabilities/recheck/",
        }
    )
    code = row["reason_code"]
    if code in ("preflight_failed", "preflight_pending"):
        return {
            "type": "preflight",
            "category": "startup",
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
    if code == "agent_tools_unread":
        if connection(pairing.runtime) != "sandbox":
            # Only an agent started from its source in our sandbox can be read.
            return None
        # Checking the pairing reads the version's tools, whether or not it is
        # certified already.
        return {
            "type": code,
            "category": "startup",
            "target": None,
            "action": "Re-check to read this agent's tools",
            "would": "This agent's tools are compared with the environment's",
            "recheck": {
                "scope": "pair",
                "method": "POST",
                "path": f"{_BASE_PATH}/{environment_id}/capabilities/recheck/",
            },
        }
    if code in _AGENT_GAP_FIXES:
        kind = "agent_tool" if code == "tool_not_in_world" else "tool"
        names = [one["name"] for one in row["targets"] if one["kind"] == kind]
        action, would = _AGENT_GAP_FIXES[code]
        # Only a new agent or environment version changes the documents compared.
        return {
            "type": code,
            "category": "tool_routing",
            "target": names[0] if names else None,
            "action": action.format(target=", ".join(names)),
            "would": would.format(target=", ".join(names)),
            "recheck": None,
        }
    if pair is not None and (
        row["status"] == "stale" or code in ("built_with_unknown", "not_run")
    ):
        return {
            "type": "recheck",
            "category": None,
            "target": None,
            "action": "Check this agent with this environment and suite",
            "would": "The row reads what this agent does",
            "recheck": pair,
        }
    if row["status"] == "untested" and row["blocked_by"]:
        gate = row["blocked_by"]
        labels = {one.id: one.label for one in CATALOGUE}
        return {
            "type": "gate",
            "category": _CATEGORIES.get(gate),
            "target": gate,
            "action": f"Fix {labels.get(gate, gate)} first",
            "would": f"{row['label']} is checked once {labels.get(gate, gate)} is Supported",
            "recheck": pair,
        }
    target = row["targets"][0]["name"] if row["targets"] else None
    action, would = _FIXES[row["id"]]
    subject = target or ("the tools" if code == "not_run" else "the failing tools")
    return {
        "type": row["id"],
        "category": _CATEGORIES.get(row["id"]),
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
    lowest = worst(
        row["status"]
        for row in rows
        # A Stop not seen yet is no fault of the pairing: it never lowers the answer.
        if row["measured"]
        and not (row["id"] == "cancel" and row["status"] == "untested")
    )
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
