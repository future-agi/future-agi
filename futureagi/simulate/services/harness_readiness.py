"""Run readiness: what an agent, environment and suite pairing is shown to support.

Computed on read, never stored per pairing. The evidence is the pairing's certified
bundle when there is one, else the environment version's build certificate, which was
measured with the agent the environment was built with. Only an observed check makes a
row Supported.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

import structlog
from django.core.cache import cache
from django.utils import timezone
from minio.error import S3Error

from simulate.models import (
    AgentVersion,
    HarnessAgentVersionRuntime,
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HarnessSuiteVersion,
    HostedHarnessJob,
)
from simulate.models.harness_versioning import ArchiveLayout, VersionStatus
from simulate.services.harness_versioning import archives, assembly, validation
from simulate.services.harness_versioning.read import (
    _agent_definition_id,
    in_workspace,
    require_versioning,
    version_refs,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    _load_ids,
    _not_pickable,
    _pin_ids,
    agent_version_pickable,
)

logger = structlog.get_logger(__name__)

CATALOG_VERSION = "1"
RULES_VERSION = "5"
TOOL_CERTIFICATION = "generic-harness/tool-certification.json"
ACTION_CERTIFICATION = "generic-harness/action-certification.json"
_MAX_CERTIFICATION_BYTES = 8 * 1024 * 1024
_BASE_PATH = "/simulate/api/harness-environments"

Status = Literal["supported", "partial", "blocked", "unavailable", "untested", "stale"]
EvidenceSource = Literal["pair_certificate", "build_certificate", "none"]
Finding = Literal["passed", "failed", "limited", "not_applicable", "not_run"]
# An area, and the answer, read as their worst row.
WORST_FIRST: tuple[Status, ...] = (
    "blocked",
    "stale",
    "untested",
    "partial",
    "unavailable",
    "supported",
)
_GATE_OPEN = frozenset({"supported", "partial"})
_SANDBOX_SOURCES = frozenset(
    {
        HarnessAgentVersionRuntime.SourceKind.GITHUB,
        HarnessAgentVersionRuntime.SourceKind.ARCHIVE,
    }
)


@dataclass(frozen=True)
class Capability:
    id: str
    label: str
    # The Ready to run panel line it shows on; ``None`` keeps it off the panel.
    area: str | None
    measured: bool
    # Read only once this row is Supported or Partial.
    gate: str | None = None


CATALOGUE: tuple[Capability, ...] = (
    Capability("start", "Agent starts", "start_and_message", True),
    Capability("message", "Takes a test message", "start_and_message", True, "start"),
    Capability("continuity", "Keeps context across turns", "start_and_message", False),
    Capability("streaming", "Streams its replies", "start_and_message", False),
    Capability("tool_discovery", "Tools discovered", "tool_binding", True, "start"),
    Capability(
        "tool_routing",
        "Controlled tool routing",
        "tool_binding",
        True,
        "tool_discovery",
    ),
    Capability("state", "Seeded world state", "evidence", True, "start"),
    Capability("audio", "Audio evidence", "evidence", False),
    Capability("files", "File evidence", "evidence", False),
    Capability("browser", "Browser evidence", "evidence", False),
    Capability("children", "Sub-agent evidence", "evidence", False),
    Capability(
        "hidden_material", "Grading material kept from the agent", "evidence", False
    ),
    Capability("reset", "World resets between cases", "reset", True, "state"),
    Capability("resume", "Resumes an interrupted case", "reset", False),
    Capability("cancel", "Stops cleanly when cancelled", "cancellation", False),
    Capability("cost", "Run cost", None, False),
)
AREAS = ("start_and_message", "tool_binding", "evidence", "reset", "cancellation")


class VersionRef(TypedDict):
    id: str
    label: str | None


class PairingView(TypedDict):
    agent_version: VersionRef | None
    environment_version: VersionRef | None
    suite_version: VersionRef | None
    evidence: EvidenceSource


class AnswerView(TypedDict):
    kind: Literal["ready", "blocked", "stale", "unchecked", "validating"]
    worst: Status
    reason_code: str | None


class AreaView(TypedDict):
    id: str
    status: Status
    rows: list[str]


class Target(TypedDict):
    kind: Literal["tool", "action", "check"]
    name: str
    result: str


class EvidenceCheck(TypedDict):
    check: str
    result: str


class FixRecheck(TypedDict):
    scope: Literal["agent", "pair"]
    method: Literal["POST"]
    path: str


class Fix(TypedDict):
    type: str
    target: str | None
    action: str
    would: str
    recheck: FixRecheck


class RowView(TypedDict):
    id: str
    label: str
    area: str | None
    status: Status
    measured: bool
    finding: Finding | None
    reason_code: str | None
    reason: str | None
    targets: list[Target]
    blocked_by: str | None
    stale_because: str | None
    was: Status | None
    evidence: list[EvidenceCheck]
    checked_at: str | None
    fix: Fix | None


class ReadinessReport(TypedDict):
    pairing: PairingView
    validation: validation.ValidationView
    answer: AnswerView
    areas: list[AreaView]
    rows: list[RowView]
    counts: dict[Status, int]
    catalog_version: str
    rules_version: str
    computed_at: str


@dataclass(frozen=True)
class Pairing:
    """An agent, environment and suite version of one environment, read for readiness.

    ``runtime`` is the agent's side row as it runs in ``environment`` (a shared legacy
    row stands for the environment's own facts), or ``None`` without one.
    """

    environment: HostedHarnessJob
    agent_version: AgentVersion | None
    environment_version: HarnessEnvironmentVersion
    suite_version: HarnessSuiteVersion
    runtime: HarnessAgentVersionRuntime | None


@dataclass(frozen=True)
class _Reading:
    """What the evidence says about one measured row, before the rules weigh it."""

    finding: Finding
    reason_code: str | None = None
    reason: str | None = None
    targets: list[Target] = field(default_factory=list)
    evidence: list[EvidenceCheck] = field(default_factory=list)


@dataclass(frozen=True)
class _Evidence:
    source: EvidenceSource
    checks: Mapping[str, Any]
    tools: list[Mapping[str, Any]]
    actions: list[Mapping[str, Any]]
    checked_at: str | None


# --- the pairing ------------------------------------------------------------------------


def resolve_pairing(
    environment: HostedHarnessJob,
    *,
    agent_version_id: Any = None,
    environment_version_id: Any = None,
    suite_version_id: Any = None,
) -> Pairing:
    """The pairing these ids name; an absent id is the environment's default.

    Unlike a run's picks, an agent version whose preflight failed or has not finished
    is resolved: readiness reads it as its start being Blocked.

    Raises:
        HostedHarnessError: 404 ``versioning_disabled`` when the environment has no
            versions to pair; 422 ``version_not_pickable`` for a version that is not
            one of this environment's.
    """
    require_versioning()
    if (
        environment.environment_version_id is None
        or environment.suite_version_id is None
    ):
        raise HostedHarnessError(
            "versioning_disabled",
            "this environment has no versions yet",
            status_code=404,
        )
    defaults = _pin_ids(environment)
    picked = (agent_version_id, environment_version_id, suite_version_id)
    ids = tuple(pick or default for pick, default in zip(picked, defaults, strict=True))
    for piece, model, pick, default in (
        ("environment", HarnessEnvironmentVersion, ids[1], defaults[1]),
        ("suite", HarnessSuiteVersion, ids[2], defaults[2]),
    ):
        if (
            str(pick) != str(default)
            and not model.no_workspace_objects.filter(
                job=environment, id=pick, status=VersionStatus.ACTIVE
            ).exists()
        ):
            raise _not_pickable(piece)
    if ids[0] and str(ids[0]) != str(defaults[0]):
        _require_environments_agent(environment, ids[0])
    agent_version, environment_version, suite_version, runtime = _load_ids(
        environment, ids
    )
    return Pairing(
        environment=environment,
        agent_version=agent_version,
        environment_version=environment_version,
        suite_version=suite_version,
        runtime=runtime,
    )


def _require_environments_agent(environment: HostedHarnessJob, agent_id: Any) -> None:
    from simulate.services.harness_agent_versions import lineage_version_ids

    definition_id = _agent_definition_id(environment)
    found = (
        definition_id is not None
        and AgentVersion.no_workspace_objects.filter(
            in_workspace(environment),
            id=agent_id,
            organization_id=environment.organization_id,
            agent_definition_id=definition_id,
        ).exists()
    )
    allowed = lineage_version_ids(environment, definition_id) if found else None
    discarded = HarnessAgentVersionRuntime.no_workspace_objects.filter(
        agent_version_id=agent_id,
        preflight_status=HarnessAgentVersionRuntime.PreflightStatus.DISCARDED,
    ).exists()
    if not found or discarded or (allowed is not None and str(agent_id) not in allowed):
        raise _not_pickable("agent")


def pair_bundle(pairing: Pairing) -> HarnessAssembledBundle | None:
    """The pairing's bundle cache row, as a run of it would look it up."""
    if pairing.runtime is None:
        return None
    key = assembly.bundle_key(
        pairing.runtime, pairing.environment_version, pairing.suite_version
    )
    return HarnessAssembledBundle._base_manager.filter(
        job_id=pairing.environment.id, assembly_key=key
    ).first()


# --- evidence ---------------------------------------------------------------------------


def _ready(bundle: HarnessAssembledBundle | None) -> bool:
    return (
        bundle is not None
        and bundle.state == HarnessAssembledBundle.State.READY
        and bool(bundle.object_key)
    )


def _certification_files(object_key: str) -> dict[str, list[Mapping[str, Any]]]:
    """The per-tool and per-action certification an archive holds, read once per
    object: archives are content-addressed, so a key never changes what it holds."""
    cache_key = (
        "harness-readiness-certification:"
        + hashlib.sha256(object_key.encode()).hexdigest()
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        found = archives.read_members(
            archives.get_archive(object_key),
            {TOOL_CERTIFICATION, ACTION_CERTIFICATION},
            _MAX_CERTIFICATION_BYTES,
        )
    except (S3Error, tarfile.TarError) as exc:
        logger.warning(
            "harness_readiness_certification_unreadable",
            object_key=object_key,
            error=type(exc).__name__,
        )
        return {"tools": [], "actions": []}
    files = {
        "tools": _items(_json(found.get(TOOL_CERTIFICATION)), "tools"),
        "actions": _items(_json(found.get(ACTION_CERTIFICATION)), "actions"),
    }
    cache.set(cache_key, files, timeout=None)
    return files


def _json(body: bytes | None) -> Any:
    try:
        return json.loads(body) if body else None
    except ValueError:
        return None


def _items(document: Any, name: str) -> list[Mapping[str, Any]]:
    values = document.get(name) if isinstance(document, Mapping) else None
    return [one for one in values or [] if isinstance(one, Mapping)]


def _build_certification(
    environment_version: HarnessEnvironmentVersion,
) -> Mapping[str, Any] | None:
    for output in (environment_version.documents or {}).get("stage_outputs") or []:
        if isinstance(output, Mapping) and output.get("kind") == "certification":
            data = output.get("data")
            return data if isinstance(data, Mapping) else None
    return None


def _evidence(pairing: Pairing, bundle: HarnessAssembledBundle | None) -> _Evidence:
    """A certified pair bundle, else the environment version's build certificate."""
    if _ready(bundle):
        files = _certification_files(bundle.object_key)
        checks = (bundle.certificate or {}).get("checks")
        return _Evidence(
            source="pair_certificate",
            checks=checks if isinstance(checks, Mapping) else {},
            tools=files["tools"],
            actions=files["actions"],
            checked_at=bundle.updated_at.isoformat(),
        )
    version = pairing.environment_version
    data = _build_certification(version)
    if data is None:
        return _Evidence("none", {}, [], [], None)
    certificate = data.get("certificate")
    checks = certificate.get("checks") if isinstance(certificate, Mapping) else None
    # Only an archive kept whole holds the sealed tool certification.
    tools = (
        _certification_files(version.archive_object_key)["tools"]
        if version.archive_layout == ArchiveLayout.COMBINED
        else []
    )
    return _Evidence(
        source="build_certificate",
        checks=checks if isinstance(checks, Mapping) else {},
        tools=tools,
        actions=_items(data.get("action_certification"), "actions"),
        checked_at=version.created_at.isoformat(),
    )


def _ratio(value: Any) -> tuple[int, int] | None:
    accepted, _, total = str(value or "").partition("/")
    if accepted.isdigit() and total.isdigit():
        return int(accepted), int(total)
    return None


def _tools(evidence: _Evidence, *availability: str) -> list[str]:
    return sorted(
        str(item.get("tool") or "")
        for item in evidence.tools
        if item.get("availability") in availability
    )


def _actions(evidence: _Evidence, *statuses: str) -> list[str]:
    return sorted(
        str(item.get("action") or "")
        for item in evidence.actions
        if item.get("status") in statuses
    )


def _targets(kind: str, names: list[str], result: str) -> list[Target]:
    return [{"kind": kind, "name": name, "result": result} for name in names]


def _check_list(checks: Mapping[str, Any], *names: str) -> list[EvidenceCheck]:
    return [
        {"check": name, "result": str(checks[name])}
        for name in names
        if checks.get(name) is not None
    ]


def _single(checks: Mapping[str, Any], name: str, code: str) -> _Reading:
    """A row read from one certificate check."""
    value = checks.get(name)
    seen = _check_list(checks, name)
    if value == "failed":
        return _Reading("failed", code, f"The {name} check failed", evidence=seen)
    if value in ("passed", "not_applicable"):
        return _Reading(value, evidence=seen)
    return _Reading("not_run", evidence=seen)


def _start(evidence: _Evidence) -> _Reading:
    checks = evidence.checks
    seen = _check_list(checks, "static", "processes")
    values = [checks.get("static"), checks.get("processes")]
    if "failed" in values:
        return _Reading(
            "failed",
            "start_failed",
            "The agent did not start in the test sandbox",
            evidence=seen,
        )
    if values == ["passed", "passed"]:
        return _Reading("passed", evidence=seen)
    return _Reading("not_run", evidence=seen)


def _tool_discovery(evidence: _Evidence) -> _Reading:
    seen = _check_list(evidence.checks, "tool_contract")
    ratio = _ratio(evidence.checks.get("tool_contract"))
    rejected = _tools(evidence, "rejected")
    runtime_only = _tools(evidence, "runtime_only")
    if rejected or (ratio and ratio[0] < ratio[1]):
        return _Reading(
            "failed",
            "tool_rejected",
            (
                "Not discoverable: " + ", ".join(rejected)
                if rejected
                else f"{ratio[0]} of {ratio[1]} tools were discovered"
            ),
            _targets("tool", rejected, "rejected"),
            seen,
        )
    if runtime_only:
        return _Reading(
            "limited",
            "runtime_only",
            "Checked only during calls, not before: " + ", ".join(runtime_only),
            _targets("tool", runtime_only, "runtime_only"),
            seen,
        )
    if ratio and ratio[1] > 0:
        return _Reading("passed", evidence=seen)
    return _Reading("not_run", evidence=seen)


def _tool_routing(evidence: _Evidence, contract: Mapping[str, Any] | None) -> _Reading:
    seen = _check_list(evidence.checks, "action_behavior")
    structural = structural_findings(contract)
    if structural is not None:
        return _Reading(
            "failed",
            structural.reason_code,
            structural.reason,
            structural.targets,
            seen,
        )
    ratio = _ratio(evidence.checks.get("action_behavior"))
    failed = _actions(evidence, "failed")
    if failed or (ratio and ratio[0] < ratio[1]):
        return _Reading(
            "failed",
            "action_failed",
            (
                "Failed its probe: " + ", ".join(failed)
                if failed
                else f"{ratio[0]} of {ratio[1]} tool probes passed"
            ),
            _targets("action", failed, "failed"),
            seen,
        )
    # A refused probe is the agent declining a call it should decline: a pass.
    passed = _actions(evidence, "passed", "refused")
    not_run = _actions(evidence, "not_run")
    if passed and not_run:
        return _Reading(
            "limited",
            "probe_not_run",
            "Not probed: " + ", ".join(not_run),
            _targets("action", not_run, "not_run"),
            seen,
        )
    if passed or (ratio and ratio[1] > 0 and not not_run):
        return _Reading("passed", evidence=seen)
    return _Reading(
        "not_run", targets=_targets("action", not_run, "not_run"), evidence=seen
    )


def _reset(evidence: _Evidence, runtime: HarnessAgentVersionRuntime | None) -> _Reading:
    checks = evidence.checks
    seen = _check_list(checks, "reset_equivalence", "world_isolation")
    reset, isolation = checks.get("reset_equivalence"), checks.get("world_isolation")
    if "failed" in (reset, isolation):
        return _Reading(
            "failed",
            "reset_failed",
            "The world did not return to its seeded state",
            evidence=seen,
        )
    if reset == "not_applicable" and isolation in (None, "not_applicable"):
        return _Reading("not_applicable", evidence=seen)
    if reset != "passed":
        return _Reading("not_run", evidence=seen)
    if runtime is not None and not _started_in_sandbox(runtime):
        return _Reading(
            "limited",
            "external_agent",
            "The world resets, but the agent runs outside the test sandbox",
            evidence=seen,
        )
    if isolation == "not_run":
        return _Reading(
            "limited",
            "isolation_not_run",
            "Cases share one world at a time",
            evidence=seen,
        )
    return _Reading("passed", evidence=seen)


def _started_in_sandbox(runtime: HarnessAgentVersionRuntime) -> bool:
    from simulate.services.harness_agent_versions import _payload_source

    return _payload_source(runtime)["kind"] in _SANDBOX_SOURCES


def _readings(pairing: Pairing, evidence: _Evidence) -> dict[str, _Reading]:
    checks = evidence.checks
    contract = (pairing.environment_version.documents or {}).get("contract")
    return {
        "start": _start(evidence),
        "message": _single(checks, "chat_ingress", "message_failed"),
        "tool_discovery": _tool_discovery(evidence),
        "tool_routing": _tool_routing(
            evidence, contract if isinstance(contract, Mapping) else None
        ),
        "state": _single(checks, "schema_and_seed", "state_failed"),
        "reset": _reset(evidence, pairing.runtime),
    }


# --- findings that hold whatever the certificate says -----------------------------------


@dataclass(frozen=True)
class StructuralFinding:
    reason_code: Literal["production_only", "tool_unreachable"]
    reason: str
    targets: list[Target]


def _provisionable(dependency: Mapping[str, Any]) -> bool:
    """The environment stands up its own copy of a dependency only when it knows the
    engine and how the agent reaches it (the rule the build uses)."""
    reached = dependency.get("reached") or {}
    seam = any(
        reached.get(name)
        for name in (
            "dsn_env",
            "config_key",
            "host",
            "port",
            "database",
            "loader_module",
        )
    )
    return bool(dependency.get("engine")) and seam


def structural_findings(
    contract: Mapping[str, Any] | None,
) -> StructuralFinding | None:
    """What the environment version's contract alone shows tool routing cannot do.

    A tool the build recorded as unreachable, or a service-backed tool naming no
    service, cannot be routed to the test world. A tool using a dependency the
    environment cannot stand up reaches the real one: production-only.
    """
    contract = contract or {}
    unroutable = sorted(
        str(entry.get("tool") or "")
        for entry in contract.get("tool_entrypoints") or []
        if isinstance(entry, Mapping)
        and (
            entry.get("mode") == "unreachable"
            or (entry.get("mode") == "service" and not entry.get("service"))
        )
    )
    if unroutable:
        return StructuralFinding(
            "tool_unreachable",
            "No reachable implementation for: " + ", ".join(unroutable),
            _targets("tool", unroutable, "failed"),
        )
    live = sorted(
        {
            str(tool)
            for dependency in contract.get("dependencies") or []
            if isinstance(dependency, Mapping) and not _provisionable(dependency)
            for tool in dependency.get("used_by") or []
        }
    )
    if live:
        verb = "reaches" if len(live) == 1 else "reach"
        return StructuralFinding(
            "production_only",
            f"Unsupported for this controlled test: {', '.join(live)} {verb} production",
            _targets("tool", live, "failed"),
        )
    return None


def preflight_findings(
    runtime: HarnessAgentVersionRuntime | None,
) -> _Reading | None:
    """A failed or unfinished agent preflight: the agent cannot start, whatever the
    certificate says. The reason names the checks that failed and what they miss."""
    if runtime is None:
        return None
    status = runtime.preflight_status
    if status == HarnessAgentVersionRuntime.PreflightStatus.PENDING:
        return _Reading(
            "failed", "preflight_pending", "The agent's preflight has not finished"
        )
    if status != HarnessAgentVersionRuntime.PreflightStatus.FAILED:
        return None
    report = runtime.preflight_report or {}
    failed = [
        check
        for check in report.get("checks") or []
        if isinstance(check, Mapping) and check.get("status") == "failed"
    ]
    missing = sorted(
        {str(name) for check in failed for name in check.get("missing") or []}
    )
    names = ", ".join(str(check.get("label") or check.get("id")) for check in failed)
    return _Reading(
        "failed",
        "preflight_failed",
        f"Agent preflight failed: {names or 'agent preflight'}"
        + (f" (missing {', '.join(missing)})" if missing else ""),
        _targets("check", [str(check.get("id")) for check in failed], "failed"),
    )


# --- the rules --------------------------------------------------------------------------


def _status(reading: _Reading) -> tuple[Status, str | None, str | None]:
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
    reading: _Reading | None = None,
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


def _rows(
    pairing: Pairing,
    evidence: _Evidence,
    bundle: HarnessAssembledBundle | None,
) -> list[RowView]:
    """One row per capability, first matching rule wins.

    Unmeasured rows are Untested. ``start`` is Blocked on a failed or unfinished
    preflight. Then: (1) a failed pair blocks tool routing on the actions it failed;
    (3) evidence measured with another agent is Stale; (4) a gate not Supported or
    Partial leaves the row Untested; (5-9) the row's own reading.
    """
    readings = _readings(pairing, evidence)
    preflight = preflight_findings(pairing.runtime)
    failed_actions = _failed_actions(bundle)
    stale = _stale(pairing, evidence)
    rows: list[RowView] = []
    statuses: dict[str, Status] = {}
    fresh: dict[str, Status] = {}
    for capability in CATALOGUE:
        reading = readings.get(capability.id)
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
        elif capability.id == "tool_routing" and failed_actions:
            names = [one["action"] for one in failed_actions]
            failed = _Reading(
                "failed",
                "action_failed",
                "Failed its probe: " + ", ".join(names),
                _targets("action", names, "failed"),
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
    capability: Capability, reading: _Reading, statuses: Mapping[str, Status]
) -> tuple[Status, str | None, str | None, str | None]:
    gate = capability.gate
    if gate is not None and statuses.get(gate) not in _GATE_OPEN:
        return "untested", "blocked_by", f"Waits on {gate}", gate
    return (*_status(reading), None)


def _failed_actions(bundle: HarnessAssembledBundle | None) -> list[Mapping[str, Any]]:
    if bundle is None or bundle.state != HarnessAssembledBundle.State.FAILED:
        return []
    return [
        one
        for one in (bundle.error or {}).get("failed_actions") or []
        if isinstance(one, Mapping) and one.get("action")
    ]


def _stale(
    pairing: Pairing, evidence: _Evidence
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
        return {
            "type": "gate",
            "target": gate,
            "action": f"Fix {gate} first",
            "would": f"{row['id']} is checked once {gate} is Supported",
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


# --- validation -------------------------------------------------------------------------


def _validation(
    pairing: Pairing, bundle: HarnessAssembledBundle | None
) -> validation.ValidationView:
    """Where the pairing's validation stands; a ready or failed bundle reads so even
    where validation cannot run now."""
    job = (
        HostedHarnessJob.no_workspace_objects.filter(
            assembled_bundle_id=bundle.id, payload__metadata__certify_only=True
        )
        .order_by("-created_at")
        .first()
        if bundle is not None
        else None
    )
    error = (bundle.error or {}) if bundle is not None else {}
    if _ready(bundle):
        state = "ready"
    elif bundle is not None and bundle.state == HarnessAssembledBundle.State.FAILED:
        state = "failed"
    elif bundle is not None and validation.live_validation(bundle.id) is not None:
        state = "validating"
    elif pairing.agent_version is None or not validation.can_validate(
        pairing.environment, pairing.agent_version.id
    ):
        state = "unavailable"
    elif error.get("last_attempt"):
        state = "retry"
    else:
        state = "not_run"
    shown: dict[str, Any] | None = None
    if state == "failed":
        shown = {"code": error.get("code")}
        if "failed_actions" in error:
            shown["failed_actions"] = error["failed_actions"]
    elif state == "retry":
        shown = {"code": (error.get("last_attempt") or {}).get("code")}
    metadata = ((job.payload or {}).get("metadata") or {}) if job else {}
    return {
        "state": state,
        "job_id": str(job.id) if job is not None else None,
        "purpose": metadata.get("validation_purpose"),
        "finished_at": (
            job.terminal_at.isoformat() if job and job.terminal_at else None
        ),
        "error": shown,
    }


def recheck(pairing: Pairing) -> validation.ValidationView:
    """Validate the pairing again; moves no default.

    A failed pairing is reset to pending first. ``unavailable`` when validation cannot
    run here, or the agent cannot start until its preflight passes.
    """
    agent = pairing.agent_version
    started = (
        validation.start_validation(
            pairing.environment,
            agent,
            pairing.environment_version,
            pairing.suite_version,
            purpose="recheck",
        )
        if agent is not None and agent_version_pickable(pairing.environment, agent.id)
        else None
    )
    if started is None:
        return {
            "state": "unavailable",
            "job_id": None,
            "purpose": "recheck",
            "finished_at": None,
            "error": None,
        }
    return started.as_view()


# --- the report -------------------------------------------------------------------------


def readiness_report(pairing: Pairing) -> ReadinessReport:
    """The pairing's readiness, computed now from its evidence. No writes."""
    bundle = pair_bundle(pairing)
    evidence = _evidence(pairing, bundle)
    rows = _rows(pairing, evidence, bundle)
    view = _validation(pairing, bundle)
    refs = version_refs(
        pairing.agent_version, pairing.environment_version, pairing.suite_version
    )
    by_id = {row["id"]: row for row in rows}
    areas: list[AreaView] = [
        {
            "id": area,
            "status": _worst(by_id[one.id]["status"] for one in members),
            "rows": [one.id for one in members],
        }
        for area in AREAS
        if (members := [one for one in CATALOGUE if one.area == area])
    ]
    counts: dict[Status, int] = dict.fromkeys(WORST_FIRST, 0)
    for row in rows:
        counts[row["status"]] += 1
    return {
        "pairing": {
            "agent_version": refs["agent"],
            "environment_version": refs["environment"],
            "suite_version": refs["suite"],
            "evidence": evidence.source,
        },
        "validation": view,
        "answer": _answer(
            rows,
            validating=view["state"] == "validating",
            failed=view["state"] == "failed",
            named=bool(_failed_actions(bundle)),
        ),
        "areas": areas,
        "rows": rows,
        "counts": counts,
        "catalog_version": CATALOG_VERSION,
        "rules_version": RULES_VERSION,
        "computed_at": timezone.now().isoformat(),
    }


def _worst(statuses: Iterable[Status]) -> Status:
    found = set(statuses)
    return next(status for status in WORST_FIRST if status in found)


def _answer(
    rows: list[RowView], *, validating: bool, failed: bool, named: bool
) -> AnswerView:
    """Read from the measured rows: unmeasured ones hold no case back. A failed pair
    whose failure names no action blocks the answer without blocking a row."""
    worst = _worst(row["status"] for row in rows if row["measured"])
    pair_failed = failed and not named
    if validating:
        kind = "validating"
    elif worst == "blocked" or pair_failed:
        kind = "blocked"
    elif worst == "stale":
        kind = "stale"
    elif worst == "untested":
        kind = "unchecked"
    else:
        kind = "ready"
    return {
        "kind": kind,
        "worst": worst,
        "reason_code": "pair_failed" if pair_failed else None,
    }


def run_snapshot(
    environment: HostedHarnessJob,
    *,
    agent_version: AgentVersion | None,
    environment_version: HarnessEnvironmentVersion,
    suite_version: HarnessSuiteVersion,
    runtime: HarnessAgentVersionRuntime | None,
) -> ReadinessReport:
    """The readiness a new run keeps, for the versions it is pinned to."""
    return readiness_report(
        Pairing(
            environment=environment,
            agent_version=agent_version,
            environment_version=environment_version,
            suite_version=suite_version,
            runtime=runtime,
        )
    )
