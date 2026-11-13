"""The evidence a pairing's rows are read from, and what each measured row reads in it.

A certified pair bundle is the pairing's own evidence; without one, the environment
version's build certificate, measured with the agent it was built with.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import structlog
from django.core.cache import cache
from django.db.models import F

from simulate.models import (
    HarnessAgentVersionRuntime,
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HarnessSuiteVersion,
    HostedHarnessArtifact,
    HostedHarnessAttempt,
    HostedHarnessJob,
    HostedHarnessReceipt,
)
from simulate.models.harness_versioning import ArchiveLayout
from simulate.services.harness_readiness.catalogue import (
    EvidenceCheck,
    EvidenceSource,
    Pairing,
    Reading,
    targets,
)
from simulate.services.harness_versioning import archives, assembly
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

TOOL_CERTIFICATION = assembly.TOOL_CERTIFICATION
ACTION_CERTIFICATION = assembly.ACTION_CERTIFICATION
# Each tool call the qualification conversation made that reached the test world.
TOOL_ROUTING = assembly.TOOL_ROUTING
_MAX_CERTIFICATION_BYTES = 8 * 1024 * 1024
# An archive's files never change, so they are kept long; an unreadable one is tried
# again soon.
_FILES_TTL_SECONDS = 7 * 24 * 3600
_UNREADABLE_TTL_SECONDS = 300
# One reader per archive at a time, for as long as a read may take; the others wait up
# to 10 s (in the request) for its result, then are told to try again.
_READING_TTL_SECONDS = 300
_WAIT_STEP_SECONDS = 0.25
_WAIT_STEPS = 40
_RETRY_AFTER_SECONDS = 10
_SANDBOX_SOURCES = frozenset(
    {
        HarnessAgentVersionRuntime.SourceKind.GITHUB,
        HarnessAgentVersionRuntime.SourceKind.ARCHIVE,
    }
)


# The per-tool and per-action certification an archive holds, and whether it was read.
CertificationFiles = dict[str, list[Mapping[str, Any]]]
_Read = tuple[CertificationFiles, bool]


@dataclass(frozen=True)
class Evidence:
    source: EvidenceSource
    checks: Mapping[str, Any]
    tools: list[Mapping[str, Any]]
    actions: list[Mapping[str, Any]]
    checked_at: str | None
    # Tools observed reaching the test world in the qualification conversation.
    routed: list[Mapping[str, Any]] = field(default_factory=list)
    # Tools qualification did not see reaching the world, each with why.
    unobserved: list[Mapping[str, Any]] = field(default_factory=list)
    # A build certificate of an earlier environment version than the pairing's, read
    # because the pairing's own version has none.
    made_for: HarnessEnvironmentVersion | None = None
    # Whether a build certificate covered the pairing's suite version; ``None`` when
    # it cannot be told.
    covers_suite: bool | None = None
    # The certification archive could not be read, so its rows read as unmeasured.
    unreadable: bool = False


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


def certified(bundle: HarnessAssembledBundle | None) -> bool:
    return (
        bundle is not None
        and bundle.state == HarnessAssembledBundle.State.READY
        and bool(bundle.object_key)
    )


_FILES = ("tools", "actions", "routed", "unobserved")


def _certification_files(
    row: HarnessAssembledBundle | HarnessEnvironmentVersion, object_key: str
) -> dict[str, Any]:
    """The per-tool and per-action certification and tool routing of the archive at
    ``object_key``, kept on ``row``. A bundle stored since they are kept has them
    already; an older row has its archive read once, and keeps what was read. An
    archive that cannot be read is marked ``unreadable``."""
    kept = row.certification_files
    if isinstance(kept, Mapping) and kept.get("object_key") == object_key:
        return {name: _items(kept, name) for name in _FILES}
    files, readable = _read_once(object_key)
    if not readable:
        return {**files, "unreadable": True}
    key_field = (
        "object_key"
        if isinstance(row, HarnessAssembledBundle)
        else "archive_object_key"
    )
    type(row)._base_manager.filter(id=row.id, **{key_field: object_key}).update(
        certification_files={"object_key": object_key, **files}
    )
    return files


def _read_once(object_key: str) -> _Read:
    """The archive's certification files and whether it could be read, by one reader
    per archive: archives are content-addressed, so a key never changes what it holds.
    A request that finds another reading waits for its result, and is told to try again
    when it does not come in time. An archive that cannot be read reads as holding
    neither, for a few minutes."""
    cache_key = (
        "harness-readiness-certification:"
        + hashlib.sha256(object_key.encode()).hexdigest()
    )
    cached: _Read | None = cache.get(cache_key)
    if cached is not None:
        return cached
    reading = cache_key + ":reading"
    if not cache.add(reading, 1, timeout=_READING_TTL_SECONDS):
        for _ in range(_WAIT_STEPS):
            time.sleep(_WAIT_STEP_SECONDS)
            cached = cache.get(cache_key)
            if cached is not None:
                return cached
        raise HostedHarnessError(
            "certification_reading",
            "This pairing's certification is still being read. Try again in a few "
            "seconds.",
            status_code=503,
            retryable=True,
            details={"retry_after": _RETRY_AFTER_SECONDS},
        )
    try:
        return _read_certification_files(object_key, cache_key)
    finally:
        cache.delete(reading)


def _read_certification_files(object_key: str, cache_key: str) -> _Read:
    try:
        found = archives.stream_members(
            object_key,
            {TOOL_CERTIFICATION, ACTION_CERTIFICATION, TOOL_ROUTING},
            _MAX_CERTIFICATION_BYTES,
        )
    except Exception as exc:  # noqa: BLE001 - storage, network or a truncated archive
        logger.warning(
            "harness_readiness_certification_unreadable",
            object_key=object_key,
            error=type(exc).__name__,
        )
        unreadable: _Read = ({name: [] for name in _FILES}, False)
        cache.set(cache_key, unreadable, timeout=_UNREADABLE_TTL_SECONDS)
        return unreadable
    kept = assembly.certification_files(found, object_key)
    read = ({name: kept[name] for name in _FILES}, True)
    cache.set(cache_key, read, timeout=_FILES_TTL_SECONDS)
    return read


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


def agent_tools(pairing: Pairing) -> list[Mapping[str, Any]] | None:
    """The tools the picked agent version's stored document lists; ``None`` when no
    document of it was read, or it no longer matches the agent's provider."""
    from simulate.services.agent_setup import stored_agent

    if pairing.agent_version is None:
        return None
    document = stored_agent(pairing.agent_version.id)
    return list(document["tools"]) if document is not None else None


def read_evidence(pairing: Pairing, bundle: HarnessAssembledBundle | None) -> Evidence:
    """A certified pair bundle, else the environment version's build certificate."""
    if certified(bundle) and bundle is not None and bundle.object_key:
        files = _certification_files(bundle, bundle.object_key)
        checks = (bundle.certificate or {}).get("checks")
        kept = (bundle.certificate or {}).get("tool_routing")
        kept = kept if isinstance(kept, Mapping) else {}
        return Evidence(
            source="pair_certificate",
            checks=checks if isinstance(checks, Mapping) else {},
            tools=files["tools"],
            actions=files["actions"],
            checked_at=bundle.updated_at.isoformat(),
            # Kept on the bundle by the check, else sealed in its archive.
            routed=kept.get("tools") or files.get("routed", []),
            unobserved=kept.get("unobserved") or files.get("unobserved", []),
            unreadable=bool(files.get("unreadable")),
        )
    version = _certified_version(pairing.environment_version)
    data = _build_certification(version) if version is not None else None
    if version is None or data is None:
        return Evidence("none", {}, [], [], None)
    certificate = data.get("certificate")
    checks = certificate.get("checks") if isinstance(certificate, Mapping) else None
    # Only an archive kept whole holds the sealed tool certification.
    files = (
        _certification_files(version, version.archive_object_key)
        if version.archive_layout == ArchiveLayout.COMBINED
        else {}
    )
    tools = files.get("tools", [])
    return Evidence(
        source="build_certificate",
        checks=checks if isinstance(checks, Mapping) else {},
        tools=tools,
        actions=_items(data.get("action_certification"), "actions"),
        checked_at=version.created_at.isoformat(),
        routed=files.get("routed", []),
        unobserved=files.get("unobserved", []),
        made_for=None if version.id == pairing.environment_version.id else version,
        covers_suite=_covers_suite(version, pairing.suite_version),
        unreadable=bool(files.get("unreadable")),
    )


_ANCESTORS_READ = 50


def _certified_version(
    version: HarnessEnvironmentVersion,
) -> HarnessEnvironmentVersion | None:
    """``version``, or its nearest earlier version, that holds a build certificate: an
    edit saves a new version without certifying it."""
    seen: set[Any] = set()
    current: HarnessEnvironmentVersion | None = version
    while (
        current is not None and current.id not in seen and len(seen) < _ANCESTORS_READ
    ):
        if _build_certification(current) is not None:
            return current
        seen.add(current.id)
        current = (
            HarnessEnvironmentVersion._base_manager.filter(id=current.parent_id).first()
            if current.parent_id
            else None
        )
    return None


def _covers_suite(
    version: HarnessEnvironmentVersion, suite_version: HarnessSuiteVersion
) -> bool | None:
    """Whether the build that made ``version``'s certificate also made
    ``suite_version``: the two share that build's authoring revision, the first one
    ``version`` records."""
    revisions = (version.documents or {}).get("authoring_revisions") or []
    if not revisions:
        return None
    return revisions[0] in (
        (suite_version.documents or {}).get("authoring_revisions") or []
    )


def _ratio(value: Any) -> tuple[int, int] | None:
    accepted, _, total = str(value or "").partition("/")
    if accepted.isdigit() and total.isdigit():
        return int(accepted), int(total)
    return None


def _tools(evidence: Evidence, *availability: str) -> list[str]:
    return sorted(
        str(item.get("tool") or "")
        for item in evidence.tools
        if item.get("availability") in availability
    )


def _actions(evidence: Evidence, *statuses: str) -> list[str]:
    return sorted(
        str(item.get("action") or "")
        for item in evidence.actions
        if item.get("status") in statuses
    )


def _check_list(checks: Mapping[str, Any], *names: str) -> list[EvidenceCheck]:
    return [
        {"check": name, "result": str(checks[name])}
        for name in names
        if checks.get(name) is not None
    ]


def _single(checks: Mapping[str, Any], name: str, code: str) -> Reading:
    """A row read from one certificate check."""
    value = checks.get(name)
    seen = _check_list(checks, name)
    if value == "failed":
        return Reading("failed", code, f"The {name} check failed", evidence=seen)
    if value in ("passed", "not_applicable"):
        return Reading(value, evidence=seen)
    return Reading("not_run", evidence=seen)


def _start(evidence: Evidence) -> Reading:
    checks = evidence.checks
    seen = _check_list(checks, "static", "processes")
    values = [checks.get("static"), checks.get("processes")]
    if "failed" in values:
        return Reading(
            "failed",
            "start_failed",
            "The agent did not start in the test sandbox",
            evidence=seen,
        )
    if values == ["passed", "passed"]:
        return Reading("passed", evidence=seen)
    return Reading("not_run", evidence=seen)


def _tool_discovery(evidence: Evidence) -> Reading:
    seen = _check_list(evidence.checks, "tool_contract")
    ratio = _ratio(evidence.checks.get("tool_contract"))
    rejected = _tools(evidence, "rejected")
    runtime_only = _tools(evidence, "runtime_only")
    if rejected or (ratio and ratio[0] < ratio[1]):
        return Reading(
            "failed",
            "tool_rejected",
            (
                "Not discoverable: " + ", ".join(rejected)
                if rejected or ratio is None
                else f"{ratio[0]} of {ratio[1]} tools were discovered"
            ),
            targets("tool", rejected, "rejected"),
            seen,
        )
    if runtime_only:
        return Reading(
            "limited",
            "runtime_only",
            "Checked only during calls, not before: " + ", ".join(runtime_only),
            targets("tool", runtime_only, "runtime_only"),
            seen,
        )
    if ratio and ratio[1] > 0:
        return Reading("passed", evidence=seen)
    return Reading("not_run", evidence=seen)


def _tool_routing(evidence: Evidence) -> Reading:
    seen = _check_list(evidence.checks, "action_behavior")
    ratio = _ratio(evidence.checks.get("action_behavior"))
    failed = _actions(evidence, "failed")
    if failed or (ratio and ratio[0] < ratio[1]):
        return Reading(
            "failed",
            "action_failed",
            (
                "Failed its probe: " + ", ".join(failed)
                if failed or ratio is None
                else f"{ratio[0]} of {ratio[1]} tool probes passed"
            ),
            targets("action", failed, "failed"),
            seen,
        )
    # Routing is read only from what the test world itself received in qualification. A
    # probe that passed is the harness calling the tool, not the agent's call reaching it.
    routed = _routed(evidence, True)
    not_received = _routed(evidence, False)
    if not_received:
        return Reading(
            "failed",
            "tool_not_routed",
            "Called, but the test world never received it: " + ", ".join(not_received),
            targets("tool", not_received, "not_routed"),
            seen,
        )
    if routed:
        seen = [*seen, {"check": "tools_routed", "result": ", ".join(routed)}]
    named = {str(item.get("tool") or "") for item in evidence.tools}
    by_provider = {
        str(item.get("tool") or "")
        for item in evidence.tools
        if item.get("entrypoint_mode") == "provider"
    }
    if named and by_provider == named and not routed:
        return Reading(
            "not_applicable",
            "tools_in_provider",
            "The provider runs these tools in its own systems, so their calls never"
            " reach the test world",
            evidence=seen,
        )
    unobserved = {str(item.get("tool") or "") for item in evidence.unobserved}
    unseen = sorted(
        {*_actions(evidence, "passed", "refused", "not_run"), *unobserved}
        - {"", *routed}
    )
    probed = bool(_actions(evidence, "passed", "refused") or (ratio and ratio[0]))
    if unseen and (routed or unobserved or probed):
        return Reading(
            "limited",
            "probe_not_run",
            "Not yet shown reaching the test world: "
            + ", ".join(_why_unobserved(one, evidence) for one in unseen),
            targets("action", unseen, "not_run"),
            seen,
        )
    if routed:
        return Reading("passed", evidence=seen)
    if probed:
        return Reading(
            "limited",
            "probe_not_run",
            "Its tools work when the harness calls them; no call of the agent's was"
            " recorded reaching the test world",
            evidence=seen,
        )
    return Reading(
        "not_run", targets=targets("action", unseen, "not_run"), evidence=seen
    )


def _routed(evidence: Evidence, routed: bool) -> list[str]:
    return sorted(
        str(item.get("tool") or "")
        for item in evidence.routed
        if item.get("routed") is routed and item.get("tool")
    )


_UNOBSERVED_REASONS = {
    "reaches_outside": "its contract says it leaves the test world, so it was not asked",
    "has_effects": "its contract says it changes something, so the check did not call it",
    "not_called": "asked, but not called",
    "request_failed": "asked, but the agent did not answer",
    "not_asked": "not asked: the check's time for tool requests ran out",
    "no_world_record": "the agent runs it itself, so the test world keeps no record of"
    " the call",
}


def _why_unobserved(tool: str, evidence: Evidence) -> str:
    reason = next(
        (
            str(item.get("reason") or "")
            for item in evidence.unobserved
            if item.get("tool") == tool
        ),
        "",
    )
    shown = _UNOBSERVED_REASONS.get(reason) or (
        "" if evidence.routed or evidence.unobserved else "no call of it was recorded"
    )
    return f"{tool} ({shown})" if shown else tool


def _reset(evidence: Evidence, runtime: HarnessAgentVersionRuntime | None) -> Reading:
    checks = evidence.checks
    seen = _check_list(checks, "reset_equivalence", "world_isolation")
    reset, isolation = checks.get("reset_equivalence"), checks.get("world_isolation")
    if "failed" in (reset, isolation):
        return Reading(
            "failed",
            "reset_failed",
            "The world did not return to its seeded state",
            evidence=seen,
        )
    if reset == "not_applicable" and isolation in (None, "not_applicable"):
        return Reading("not_applicable", evidence=seen)
    if reset != "passed":
        return Reading("not_run", evidence=seen)
    if runtime is not None and not _started_in_sandbox(runtime):
        return Reading(
            "limited",
            "external_agent",
            "The world resets, but the agent runs outside the test sandbox",
            evidence=seen,
        )
    if isolation == "not_run":
        return Reading(
            "limited",
            "isolation_not_run",
            "Cases share one world at a time",
            evidence=seen,
        )
    return Reading("passed", evidence=seen)


def _started_in_sandbox(runtime: HarnessAgentVersionRuntime) -> bool:
    from simulate.services.harness_agent_versions import _payload_source

    return _payload_source(runtime)["kind"] in _SANDBOX_SOURCES


def connection(runtime: HarnessAgentVersionRuntime | None) -> str | None:
    """Where the agent runs: ``sandbox`` (ours), ``remote`` (its own URL) or
    ``provider`` (a voice or chat provider); ``None`` without a side row."""
    if runtime is None:
        return None
    if _started_in_sandbox(runtime):
        return "sandbox"
    from simulate.services.harness_agent_versions import _payload_source

    return "remote" if _payload_source(runtime)["kind"] == "remote" else "provider"


def _cancel(pairing: Pairing) -> Reading:
    """The agent version's latest Stop of a run whose sandbox existed, as the cancel
    path recorded it. Only a sandbox the agent ran in, verified gone, confirms the
    agent stopped; an agent outside it is confirmed by its own side, not ours."""
    if pairing.agent_version is None:
        return Reading(
            "not_run", "stop_not_observed", "No run of this agent stopped yet"
        )
    attempt = (
        HostedHarnessAttempt.no_workspace_objects.filter(
            job__agent_version_id=pairing.agent_version.id,
            job__cancel_requested_at__isnull=False,
            # A run of an environment; a stopped build never ran the agent's cases.
            job__environment_id__isnull=False,
            attempt_number=F("job__current_attempt_number"),
            # A validation job never started the agent; its key is set only on them.
            job__payload__metadata__certify_only__isnull=True,
            provider_ref__isnull=False,
        )
        .exclude(provider_ref="")
        .exclude(provider_ref__startswith="pending:")
        .select_related("job")
        .order_by("-job__cancel_requested_at")
        .first()
    )
    stopped = attempt.job.cancel_requested_at if attempt is not None else None
    if attempt is None or stopped is None:
        return Reading(
            "not_run", "stop_not_observed", "No run of this agent stopped yet"
        )
    seen: list[EvidenceCheck] = [
        {"check": "stop_requested", "result": stopped.isoformat()}
    ]
    if attempt.cleanup_verified_at is None:
        return Reading(
            "limited",
            "stop_unconfirmed",
            "The last Stop was asked for; our cleanup is not confirmed yet",
            evidence=seen,
            checked_at=stopped.isoformat(),
        )
    seen.append(
        {"check": "sandbox_absent", "result": attempt.cleanup_verified_at.isoformat()}
    )
    if connection(pairing.runtime) != "sandbox":
        return Reading(
            "limited",
            "stop_unconfirmed",
            "The last Stop ended our side; the agent's own side did not confirm it",
            evidence=seen,
            checked_at=attempt.cleanup_verified_at.isoformat(),
        )
    return Reading(
        "passed", evidence=seen, checked_at=attempt.cleanup_verified_at.isoformat()
    )


def readings(pairing: Pairing, evidence: Evidence) -> dict[str, Reading]:
    """What the certificate says about each measured row; ``cancel`` reads the agent
    version's last Stop instead."""
    checks = evidence.checks
    return {
        "cancel": _cancel(pairing),
        "start": _start(evidence),
        "message": _single(checks, "chat_ingress", "message_failed"),
        "tool_discovery": _tool_discovery(evidence),
        "tool_routing": _tool_routing(evidence),
        "state": _single(checks, "schema_and_seed", "state_failed"),
        "reset": _reset(evidence, pairing.runtime),
    }


# --- rows read from this agent's runs, never from a check -------------------------------

_RUNS_READ = 20
# A connection that answers each message whole: the generated chat ingress (ALK
# ``RuntimeInterface``). SSE and WebSocket are not one of them until qualified.
_WHOLE_REPLY_KINDS = frozenset({"http", "callable", "command"})


def _runs(pairing: Pairing) -> list[Any]:
    """The newest runs of the pairing's agent and environment version."""
    if pairing.agent_version is None:
        return []
    return list(
        HostedHarnessJob.no_workspace_objects.filter(
            environment_id=pairing.environment.id,
            agent_version_id=pairing.agent_version.id,
            environment_version_id=pairing.environment_version.id,
            test_execution_id__isnull=False,
            # A validation job never ran the agent's cases; its key is set only on them.
            payload__metadata__certify_only__isnull=True,
        )
        .order_by("-created_at")
        .values_list("id", flat=True)[:_RUNS_READ]
    )


def _contract(pairing: Pairing) -> Mapping[str, Any]:
    contract = (pairing.environment_version.documents or {}).get("contract")
    return contract if isinstance(contract, Mapping) else {}


def _continuity(runs: list[Any]) -> Reading:
    receipt = (
        HostedHarnessReceipt.no_workspace_objects.filter(
            job_id__in=runs, body__call__turns__gte=2
        )
        .order_by("-created_at")
        .first()
    )
    if receipt is None:
        return Reading(
            "not_run",
            "no_multi_turn_run",
            "No run of this agent has gone past one turn yet",
        )
    turns = (receipt.body.get("call") or {}).get("turns")
    return Reading(
        "limited",
        "turns_observed",
        f"A run went {turns} turns; whether the agent kept the context is not checked",
        evidence=[{"check": "turns", "result": str(turns)}],
        checked_at=receipt.created_at.isoformat(),
    )


def _streaming(pairing: Pairing) -> Reading:
    contract = _contract(pairing)
    interface = (contract.get("runtime") or {}).get("interface") or {}
    kind = str(interface.get("kind") or "") if isinstance(interface, Mapping) else ""
    if str(contract.get("modality") or "").lower() == "voice":
        return Reading(
            "not_applicable",
            "voice_call",
            "A voice agent is heard over its call, not over a chat stream",
        )
    if kind in _WHOLE_REPLY_KINDS:
        return Reading(
            "not_applicable",
            "whole_replies",
            "This connection answers each message whole; a reply ends when it returns",
            evidence=[{"check": "interface", "result": kind}],
        )
    if kind:
        return Reading(
            "not_applicable",
            "transport_not_qualified",
            f"A {kind} connection is not supported until it passes qualification",
            evidence=[{"check": "interface", "result": kind}],
        )
    return Reading(
        "not_run",
        "interface_not_recorded",
        "The connection's interface is not recorded",
    )


def _audio(pairing: Pairing, runs: list[Any]) -> Reading:
    if str(_contract(pairing).get("modality") or "").lower() != "voice":
        return Reading(
            "not_applicable", "not_voice", "A chat connection carries no audio"
        )
    recording = (
        HostedHarnessArtifact.no_workspace_objects.filter(
            job_id__in=runs, kind__startswith="recording"
        )
        .order_by("-created_at")
        .first()
    )
    if recording is None:
        return Reading(
            "not_run", "no_recording", "No run of this agent has a call recording yet"
        )
    return Reading(
        "passed",
        evidence=[{"check": "recording", "result": recording.kind}],
        checked_at=recording.created_at.isoformat(),
    )


def _resume(runs: list[Any]) -> Reading:
    resumed = (
        HostedHarnessReceipt.no_workspace_objects.filter(
            job_id__in=runs, attempt_number__gt=1
        )
        .order_by("-created_at")
        .first()
    )
    if (
        resumed is None
        or not HostedHarnessReceipt.no_workspace_objects.filter(
            job_id=resumed.job_id, attempt_number__lt=resumed.attempt_number
        ).exists()
    ):
        return Reading(
            "not_run",
            "no_interrupted_run",
            "No run of this agent was interrupted and finished on a new attempt",
        )
    return Reading(
        "passed",
        evidence=[{"check": "attempt", "result": str(resumed.attempt_number)}],
        checked_at=resumed.created_at.isoformat(),
    )


def _cost(runs: list[Any]) -> Reading:
    from simulate.services.harness_usage import harness_consumption

    for run in HostedHarnessJob.no_workspace_objects.filter(id__in=runs[:1]):
        if harness_consumption(run) is not None:
            return Reading(
                "limited",
                "agent_model_cost_unmeasured",
                "Our side of the last run was measured; the agent's own model spend"
                " is not",
                checked_at=run.updated_at.isoformat(),
            )
    return Reading(
        "not_run", "no_run_measured", "No run of this agent was measured yet"
    )


def observed(pairing: Pairing) -> dict[str, Reading]:
    """What this agent's runs on this environment version show for the rows no check
    measures; they hold no case and never lower the answer."""
    runs = _runs(pairing)
    return {
        "continuity": _continuity(runs),
        "streaming": _streaming(pairing),
        "audio": _audio(pairing, runs),
        "files": Reading(
            "not_run",
            "files_not_recorded",
            "No run records the files an agent produces yet",
        ),
        "resume": _resume(runs),
        "cost": _cost(runs),
    }
