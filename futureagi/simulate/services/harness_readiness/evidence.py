"""The evidence a pairing's rows are read from, and what each measured row reads in it.

A certified pair bundle is the pairing's own evidence; without one, the environment
version's build certificate, measured with the agent it was built with.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import structlog
from django.core.cache import cache
from django.db.models import F

from simulate.models import (
    HarnessAgentVersionRuntime,
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HarnessSuiteVersion,
    HostedHarnessAttempt,
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

logger = structlog.get_logger(__name__)

TOOL_CERTIFICATION = "generic-harness/tool-certification.json"
ACTION_CERTIFICATION = "generic-harness/action-certification.json"
_MAX_CERTIFICATION_BYTES = 8 * 1024 * 1024
# An archive's files never change, so they are kept long; an unreadable one is tried
# again soon.
_FILES_TTL_SECONDS = 7 * 24 * 3600
_UNREADABLE_TTL_SECONDS = 300
_SANDBOX_SOURCES = frozenset(
    {
        HarnessAgentVersionRuntime.SourceKind.GITHUB,
        HarnessAgentVersionRuntime.SourceKind.ARCHIVE,
    }
)


@dataclass(frozen=True)
class Evidence:
    source: EvidenceSource
    checks: Mapping[str, Any]
    tools: list[Mapping[str, Any]]
    actions: list[Mapping[str, Any]]
    checked_at: str | None
    # A build certificate of an earlier environment version than the pairing's, read
    # because the pairing's own version has none.
    made_for: HarnessEnvironmentVersion | None = None
    # Whether a build certificate covered the pairing's suite version; ``None`` when
    # it cannot be told.
    covers_suite: bool | None = None


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


def _certification_files(object_key: str) -> dict[str, list[Mapping[str, Any]]]:
    """The per-tool and per-action certification an archive holds, read once per
    object: archives are content-addressed, so a key never changes what it holds. An
    archive that cannot be read reads as holding neither, for a few minutes."""
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
    except Exception as exc:  # noqa: BLE001 - storage, network or a truncated archive
        logger.warning(
            "harness_readiness_certification_unreadable",
            object_key=object_key,
            error=type(exc).__name__,
        )
        unreadable: dict[str, list[Mapping[str, Any]]] = {"tools": [], "actions": []}
        cache.set(cache_key, unreadable, timeout=_UNREADABLE_TTL_SECONDS)
        return unreadable
    files = {
        "tools": _items(_json(found.get(TOOL_CERTIFICATION)), "tools"),
        "actions": _items(_json(found.get(ACTION_CERTIFICATION)), "actions"),
    }
    cache.set(cache_key, files, timeout=_FILES_TTL_SECONDS)
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
    if certified(bundle):
        files = _certification_files(bundle.object_key)
        checks = (bundle.certificate or {}).get("checks")
        return Evidence(
            source="pair_certificate",
            checks=checks if isinstance(checks, Mapping) else {},
            tools=files["tools"],
            actions=files["actions"],
            checked_at=bundle.updated_at.isoformat(),
        )
    version = _certified_version(pairing.environment_version)
    data = _build_certification(version) if version is not None else None
    if data is None:
        return Evidence("none", {}, [], [], None)
    certificate = data.get("certificate")
    checks = certificate.get("checks") if isinstance(certificate, Mapping) else None
    # Only an archive kept whole holds the sealed tool certification.
    tools = (
        _certification_files(version.archive_object_key)["tools"]
        if version.archive_layout == ArchiveLayout.COMBINED
        else []
    )
    return Evidence(
        source="build_certificate",
        checks=checks if isinstance(checks, Mapping) else {},
        tools=tools,
        actions=_items(data.get("action_certification"), "actions"),
        checked_at=version.created_at.isoformat(),
        made_for=None if version.id == pairing.environment_version.id else version,
        covers_suite=_covers_suite(version, pairing.suite_version),
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
                if rejected
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
                if failed
                else f"{ratio[0]} of {ratio[1]} tool probes passed"
            ),
            targets("action", failed, "failed"),
            seen,
        )
    # A refused probe is the agent declining a call it should decline: a pass.
    passed = _actions(evidence, "passed", "refused")
    not_run = _actions(evidence, "not_run")
    if passed and not_run:
        return Reading(
            "limited",
            "probe_not_run",
            "Not probed: " + ", ".join(not_run),
            targets("action", not_run, "not_run"),
            seen,
        )
    if passed or (ratio and ratio[1] > 0 and not not_run):
        return Reading("passed", evidence=seen)
    return Reading(
        "not_run", targets=targets("action", not_run, "not_run"), evidence=seen
    )


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
        )
        .exclude(provider_ref="")
        .exclude(provider_ref__startswith="pending:")
        .select_related("job")
        .order_by("-job__cancel_requested_at")
        .first()
    )
    if attempt is None:
        return Reading(
            "not_run", "stop_not_observed", "No run of this agent stopped yet"
        )
    seen: list[EvidenceCheck] = [
        {
            "check": "stop_requested",
            "result": attempt.job.cancel_requested_at.isoformat(),
        }
    ]
    if attempt.cleanup_verified_at is None:
        return Reading(
            "limited",
            "stop_unconfirmed",
            "The last Stop was asked for; our cleanup is not confirmed yet",
            evidence=seen,
            checked_at=attempt.job.cancel_requested_at.isoformat(),
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
