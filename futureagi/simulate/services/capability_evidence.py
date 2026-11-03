"""Capability evidence a build sealed, stored once per build, and the report computed from it at read time."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import structlog
from django.utils import timezone

from simulate.models import HarnessCapabilityEvidence, HostedHarnessJob
from simulate.services.capability_status import RULES_VERSION, capability_statuses

logger = structlog.get_logger(__name__)

EVIDENCE_FILE = "capability-evidence.json"
REQUIREMENTS_FILE = "scenario-requirements.json"
_MAX_BYTES = 2 * 1024 * 1024


def _archive_json(body: bytes, name: str) -> tuple[dict[str, Any], str] | None:
    """One sealed generic-harness JSON document and its digest, or None when the build wrote none; ValueError when it
    wrote one that is not a JSON object."""
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            if (
                path.name != name
                or "generic-harness" not in path.parts
                or "certified-bundle" in path.parts
                or not member.isfile()
                or member.size > _MAX_BYTES
            ):
                continue
            source = archive.extractfile(member)
            if source is None:
                continue
            raw = source.read()
            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                document = None
            if not isinstance(document, dict):
                logger.warning("capability_document_unreadable", name=name)
                raise ValueError(f"{name} is not a JSON object")
            return document, hashlib.sha256(raw).hexdigest()
    return None


def evidence_from_archive(body: bytes) -> tuple[dict[str, Any], str] | None:
    """The sealed evidence document and its digest, or None when the build wrote none."""
    return _archive_json(body, EVIDENCE_FILE)


def _catalogue(document: dict[str, Any]) -> list[dict[str, Any]]:
    """The evidence's capabilities in the order ALK published them, each with its label, gates and fix type."""
    capabilities = document.get("capabilities") or {}
    labels = document.get("labels") or {}
    order = [key for key in document.get("order") or [] if key in capabilities]
    order += [key for key in capabilities if key not in order]
    return [
        {
            "id": key,
            "label": labels.get(key, key),
            "depends_on": capabilities[key].get("depends_on", []),
            "fix_type": capabilities[key].get("fix_type"),
        }
        for key in order
    ]


def record_capability_evidence(job: HostedHarnessJob, body: bytes) -> HarnessCapabilityEvidence | None:
    """Store a build's evidence: one row per build, refreshed by each later archive, since a build uploads its
    output more than once and the last upload holds the final certificate. Never fails the build."""
    try:
        found = evidence_from_archive(body)
        if found is None:
            return None
        document, digest = found
        try:
            requirements = _archive_json(body, REQUIREMENTS_FILE)
        except ValueError:
            # Unreadable needs leave every scenario untested; the evidence itself still stands.
            requirements = None
        row, _ = HarnessCapabilityEvidence.no_workspace_objects.update_or_create(
            job=job,
            origin=HarnessCapabilityEvidence.Origin.CERTIFICATION,
            defaults={
                "organization_id": job.organization_id,
                "workspace_id": job.workspace_id,
                "agent_version_id": job.agent_version_id,
                "environment_version_id": job.environment_version_id,
                "catalog_version": str(document.get("catalog_version") or ""),
                "catalogue": _catalogue(document),
                "inputs": document.get("inputs") or {},
                "capabilities": document.get("capabilities") or {},
                "artefacts": [{"kind": "capability_evidence", "path": EVIDENCE_FILE, "sha256": digest}],
                "scenario_requirements": (requirements[0].get("scenarios") or {}) if requirements else {},
                "capture_status": HarnessCapabilityEvidence.CaptureStatus.COMPLETE,
                "capture_error": None,
                "captured_at": timezone.now(),
            },
        )
        return row
    except Exception as exc:  # noqa: BLE001 - evidence is a report, never a reason to fail a build
        logger.exception("capability_evidence_not_recorded", job_id=str(job.id))
        # Kept as an errored capture, so the report says the build's evidence could not be read rather than none.
        try:
            HarnessCapabilityEvidence.no_workspace_objects.update_or_create(
                job=job,
                origin=HarnessCapabilityEvidence.Origin.CERTIFICATION,
                defaults={
                    "organization_id": job.organization_id,
                    "workspace_id": job.workspace_id,
                    "agent_version_id": job.agent_version_id,
                    "environment_version_id": job.environment_version_id,
                    "capture_status": HarnessCapabilityEvidence.CaptureStatus.ERRORED,
                    "capture_error": type(exc).__name__,
                    "captured_at": timezone.now(),
                },
            )
        except Exception:  # noqa: BLE001 - the build still never fails for its report
            logger.exception("capability_evidence_error_not_recorded", job_id=str(job.id))
        return None


def capability_report(
    catalogue: list[dict[str, Any]],
    evidence: HarnessCapabilityEvidence | None,
    current_inputs: dict[str, Any],
) -> dict[str, Any]:
    """The report for one agent version and environment version: one status per capability, computed now."""
    document = (
        {"inputs": evidence.inputs, "capabilities": evidence.capabilities} if evidence is not None else None
    )
    return {
        "catalog_version": evidence.catalog_version if evidence is not None else None,
        "checked_at": evidence.captured_at.isoformat() if evidence is not None else None,
        "capabilities": capability_statuses(catalogue, document, current_inputs),
        "rules_version": RULES_VERSION,
        "computed_at": timezone.now().isoformat(),
    }


_BLOCKING = {"blocked", "unavailable"}
_UNKNOWN = {"untested", "stale"}


def _short(check: dict[str, Any]) -> bool:
    accepted, _, total = str(check.get("result") or "").partition("/")
    return accepted.isdigit() and total.isdigit() and int(accepted) < int(total)


def _scenario_state(row: dict[str, Any], tools: set[str]) -> str:
    """How one capability stands for one scenario. A Partial capability holds only when none of its failed targets
    is a tool the scenario needs; when the failed targets were never recorded, it is unmeasured, not passed. A
    Blocked capability whose failure names its tools blocks only the scenarios that need one of them."""
    if row["status"] == "blocked":
        targets = {check["target"] for check in row.get("checks") or [] if check.get("result") == "failed"
                   and check.get("target")}
        return "blocked" if not targets or targets & tools else "supported"
    if row["status"] != "partial":
        return row["status"]
    short = [check for check in row.get("checks") or [] if _short(check)]
    if any("failed" not in check for check in short):
        return "untested"
    failed = {name for check in short for name in check["failed"]}
    return "blocked" if failed & tools else "supported"


def affected_scenarios(rows: list[dict[str, Any]], requirements: dict[str, Any]) -> dict[str, Any]:
    """Counts from what each scenario actually needs: never estimated. Each row gains `needed_by` and `affected`
    (the scenarios it blocks, named in `affected_scenarios`); every scenario lands in exactly one of eligible, blocked
    and untested, blocked winning over untested."""
    by_id = {row["id"]: row for row in rows}
    for row in rows:
        row["needed_by"] = row["affected"] = 0
        row["affected_scenarios"] = []
    counts = {"requested": 0, "eligible": 0, "blocked": 0, "untested": 0}
    for key, value in sorted((requirements or {}).items()):
        if value is None:
            counts["requested"] += 1
            counts["untested"] += 1
            continue
        tools = set(((value or {}).get("tools") or {}).get("required") or [])
        states = set()
        for capability in (value or {}).get("capabilities") or []:
            row = by_id.get(capability)
            state = _scenario_state(row, tools) if row is not None else "untested"
            states.add(state)
            if row is not None:
                row["needed_by"] += 1
                if state in _BLOCKING:
                    row["affected"] += 1
                    row["affected_scenarios"].append(key)
        counts["requested"] += 1
        if states & _BLOCKING:
            counts["blocked"] += 1
        elif states & _UNKNOWN:
            counts["untested"] += 1
        else:
            counts["eligible"] += 1
    return {**counts, "requirements_recorded": bool(requirements)}


def structural_findings(contract: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """What the current contract alone shows cannot work, with no run: a tool the build recorded as unreachable, or
    a service-backed tool that names no service, cannot be routed to the world."""
    unroutable = [
        str(entry.get("tool") or "")
        for entry in (contract or {}).get("tool_entrypoints") or []
        if isinstance(entry, dict)
        and (entry.get("mode") == "unreachable" or (entry.get("mode") == "service" and not entry.get("service")))
    ]
    if not unroutable:
        return {}
    return {
        "tool_routing": {
            "finding": "failed",
            "reason_code": "tool_unreachable",
            "reason": "No reachable implementation for: " + ", ".join(sorted(unroutable)),
            "fix": "Bind each listed tool to its implementation in the agent's source, or to a service the "
            "environment runs, then rebuild",
            "checks": [{"target": tool, "result": "failed"} for tool in sorted(unroutable)],
        }
    }


def preflight_findings(report: dict[str, Any] | None, status: str) -> dict[str, dict[str, Any]]:
    """A failed agent preflight (source, credentials, packaging or the target itself) means the agent cannot start, so
    the capability that gates every other one reads Blocked, naming the checks that failed."""
    if status != "failed":
        return {}
    failed = [check for check in (report or {}).get("checks") or [] if check.get("status") == "failed"]
    missing = sorted({str(name) for check in failed for name in check.get("missing") or []})
    labels = ", ".join(str(check.get("label") or check.get("id")) for check in failed) or "agent preflight"
    return {
        "start_health": {
            "origin": "preflight",
            "finding": "failed",
            "reason_code": str(failed[0].get("id")) if failed else str((report or {}).get("error") or "preflight_failed"),
            "reason": f"Agent preflight failed: {labels}" + (f" (missing {', '.join(missing)})" if missing else ""),
            "fix": "Fix what the preflight names in the agent's connection, then save it as a new agent version",
            "checks": [{"target": str(check.get("id")), "result": "failed"} for check in failed],
        }
    }


def agent_version_of(job: HostedHarnessJob, agent_version_id: Any) -> Any:
    """One of this environment's agent versions, or `version_not_found`: a version id never reads another workspace's
    agent."""
    from simulate.models import AgentVersion
    from simulate.services.harness_versioning.read import version_not_found

    definition = getattr(job.agent_version, "agent_definition_id", None) if job.agent_version_id else None
    version = AgentVersion.no_workspace_objects.filter(
        id=agent_version_id, organization_id=job.organization_id, agent_definition_id=definition
    ).first() if definition else None
    if version is None:
        raise version_not_found()
    return version


def _environment_rows(
    job: HostedHarnessJob, agent_version_id: Any = None, environment_version: Any = None
) -> tuple[HarnessCapabilityEvidence | None, list[dict[str, Any]], dict[str, str]]:
    """An environment's latest build evidence and one status row per capability, for an agent version and an
    environment version (the environment's own when none is named): Stale once the environment version is another
    one or the agent is not the one it was checked with."""
    if agent_version_id:
        agent_version_id = agent_version_of(job, agent_version_id).id
    version = environment_version or job.environment_version
    evidence = (
        HarnessCapabilityEvidence.no_workspace_objects.filter(
            job=job,
            origin=HarnessCapabilityEvidence.Origin.CERTIFICATION,
            capture_status=HarnessCapabilityEvidence.CaptureStatus.COMPLETE,
        )
        .order_by("-captured_at")
        .first()
    )
    current = {
        "environment_version_id": str(getattr(version, "id", None) or ""),
        "agent_version_id": str(agent_version_id or job.agent_version_id or ""),
    }
    document = (
        {
            "inputs": {
                **evidence.inputs,
                # Evidence that names no version (captured before versions existed) cannot be judged on one, so only
                # a named one is compared.
                **(
                    {"environment_version_id": str(evidence.environment_version_id)}
                    if evidence.environment_version_id
                    else {}
                ),
                **({"agent_version_id": str(evidence.agent_version_id)} if evidence.agent_version_id else {}),
            },
            "capabilities": evidence.capabilities,
        }
        if evidence is not None
        else None
    )
    from simulate.models import HarnessAgentVersionRuntime

    structural = structural_findings((version.documents or {}).get("contract") if version is not None else None)
    runtime = HarnessAgentVersionRuntime.no_workspace_objects.filter(
        agent_version_id=current["agent_version_id"] or None
    ).first()
    if runtime is not None:
        structural.update(preflight_findings(runtime.preflight_report, runtime.preflight_status))
    rows = capability_statuses(evidence.catalogue if evidence is not None else [], document, current, structural)
    return evidence, rows, current


def _picked(
    requirements: dict[str, Any], suite_version: Any, keys: list[str] | None
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """The recorded needs of the scenarios asked about: the picked suite version's members (all of the recorded ones
    when none is picked), narrowed to `keys`; and those recorded but no longer in the suite, apart, as dropped."""
    if suite_version is None:
        members = set(requirements)
    else:
        from simulate.models import HarnessSuiteMember

        members = set(
            HarnessSuiteMember.no_workspace_objects.filter(suite_version=suite_version).values_list("scenario_key", flat=True)
        )
    if keys:
        from simulate.services.hosted_harness import HostedHarnessError

        unknown = sorted(set(keys) - members)
        if unknown:
            raise HostedHarnessError(
                "selection_invalid", "Not scenarios of that suite version", status_code=400, details={"keys": unknown}
            )
        members &= set(keys)
    # None for a scenario whose needs were never recorded: it is untested, never eligible.
    picked = {key: requirements.get(key) for key in sorted(members)}
    dropped = [
        {"key": key, "reason": "no longer in the suite"} for key in sorted(set(requirements) - members) if not keys
    ]
    return picked, dropped


def environment_capability_report(
    job: HostedHarnessJob,
    agent_version_id: Any = None,
    environment_version: Any = None,
    *,
    suite_version: Any = None,
    keys: list[str] | None = None,
) -> dict[str, Any]:
    """The capability report for an environment, an agent version and an environment version (see `_environment_rows`),
    counted over the picked suite version's scenarios (or `keys` of them)."""
    evidence, rows, current = _environment_rows(job, agent_version_id, environment_version)
    picked, dropped = _picked(evidence.scenario_requirements if evidence is not None else {}, suite_version, keys)
    selection = {**affected_scenarios(rows, picked), "dropped": dropped}
    errored = HarnessCapabilityEvidence.no_workspace_objects.filter(
        job=job,
        origin=HarnessCapabilityEvidence.Origin.CERTIFICATION,
        capture_status=HarnessCapabilityEvidence.CaptureStatus.ERRORED,
    ).exists()
    return {
        "evidence_unreadable": errored,
        "environment_version_id": current["environment_version_id"] or None,
        "agent_version_id": current["agent_version_id"] or None,
        "catalog_version": evidence.catalog_version if evidence is not None else None,
        "checked_at": evidence.captured_at.isoformat() if evidence is not None else None,
        "capabilities": rows,
        "selection": selection,
        "rules_version": RULES_VERSION,
        "computed_at": timezone.now().isoformat(),
    }


def scenario_capabilities(
    job: HostedHarnessJob, agent_version: Any = None, environment_version: Any = None
) -> dict[str, dict[str, Any]]:
    """Per scenario key the build recorded needs for: the capabilities that block it (with each one's label and fix
    type) and those not yet measured, for the agent and environment versions a run is pinned to. A scenario absent here
    has no recorded needs."""
    evidence, rows, _ = _environment_rows(job, getattr(agent_version, "id", None), environment_version)
    by_id = {row["id"]: row for row in rows}
    out: dict[str, dict[str, Any]] = {}
    for key, value in ((evidence.scenario_requirements if evidence is not None else None) or {}).items():
        tools = set(((value or {}).get("tools") or {}).get("required") or [])
        blocked: list[dict[str, Any]] = []
        untested: list[str] = []
        for capability in (value or {}).get("capabilities") or []:
            row = by_id.get(capability)
            state = _scenario_state(row, tools) if row is not None else "untested"
            if state in _BLOCKING:
                blocked.append({"capability": capability, "label": row["label"], "fix_type": row.get("fix_type")})
            elif state in _UNKNOWN:
                untested.append(capability)
        out[key] = {"blocked_by": blocked, "untested": untested}
    return out


def recheck_capabilities(job: HostedHarnessJob, scope: str, check: str | None, agent_version_id: Any = None) -> dict[str, Any]:
    """Read a capability again without a run. The agent side re-runs the agent version's preflight when it failed or
    never finished (a passed one stands); the world side needs a new build until a check-only job is decided."""
    from simulate.services.harness_agent_versions import _RETRYABLE, _side_row, retry_preflight
    from simulate.services.hosted_harness import HostedHarnessError

    evidence, rows, current = _environment_rows(job, agent_version_id)
    if check and check not in {row["id"] for row in rows}:
        raise HostedHarnessError("capability_unknown", f"{check} is not a capability this report checks", status_code=400)
    if scope == "world":
        raise HostedHarnessError(
            "world_recheck_not_offered",
            "Checking the world again needs a new build for now; rebuild the environment to read it again",
            status_code=409,
        )
    version = agent_version_of(job, current["agent_version_id"])
    runtime = _side_row(version.id)
    if runtime is None or runtime.preflight_status not in _RETRYABLE:
        return {"scope": scope, "agent_version_id": str(version.id), "state": "not_needed"}
    retry_preflight(version)
    return {"scope": scope, "agent_version_id": str(version.id), "state": "started"}
