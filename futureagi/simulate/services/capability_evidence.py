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
    """One sealed generic-harness JSON document and its digest, or None when the build wrote none."""
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
                return None
            return (document, hashlib.sha256(raw).hexdigest()) if isinstance(document, dict) else None
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
        requirements = _archive_json(body, REQUIREMENTS_FILE)
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
                "captured_at": timezone.now(),
            },
        )
        return row
    except Exception:  # noqa: BLE001 - evidence is a report, never a reason to fail a build
        logger.exception("capability_evidence_not_recorded", job_id=str(job.id))
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
    is a tool the scenario needs; when the failed targets were never recorded, it is unmeasured, not passed."""
    if row["status"] != "partial":
        return row["status"]
    short = [check for check in row.get("checks") or [] if _short(check)]
    if any("failed" not in check for check in short):
        return "untested"
    failed = {name for check in short for name in check["failed"]}
    return "blocked" if failed & tools else "supported"


def affected_scenarios(rows: list[dict[str, Any]], requirements: dict[str, Any]) -> dict[str, Any]:
    """Counts from what each scenario actually needs: never estimated. Each row gains `needed_by` and `affected`
    (the scenarios it blocks); every scenario lands in exactly one of eligible, blocked and untested, blocked
    winning over untested."""
    by_id = {row["id"]: row for row in rows}
    for row in rows:
        row["needed_by"] = row["affected"] = 0
    counts = {"requested": 0, "eligible": 0, "blocked": 0, "untested": 0}
    for value in (requirements or {}).values():
        tools = set(((value or {}).get("tools") or {}).get("required") or [])
        states = set()
        for capability in (value or {}).get("capabilities") or []:
            row = by_id.get(capability)
            state = _scenario_state(row, tools) if row is not None else "untested"
            states.add(state)
            if row is not None:
                row["needed_by"] += 1
                row["affected"] += state in _BLOCKING
        counts["requested"] += 1
        if states & _BLOCKING:
            counts["blocked"] += 1
        elif states & _UNKNOWN:
            counts["untested"] += 1
        else:
            counts["eligible"] += 1
    return {**counts, "requirements_recorded": bool(requirements)}


def environment_capability_report(job: HostedHarnessJob) -> dict[str, Any]:
    """The capability report for an environment: its latest build evidence, Stale once its environment version moved on."""
    evidence = (
        HarnessCapabilityEvidence.no_workspace_objects.filter(
            job=job,
            origin=HarnessCapabilityEvidence.Origin.CERTIFICATION,
            capture_status=HarnessCapabilityEvidence.CaptureStatus.COMPLETE,
        )
        .order_by("-captured_at")
        .first()
    )
    current = {"environment_version_id": str(job.environment_version_id or "")}
    document = (
        {
            "inputs": {**evidence.inputs, "environment_version_id": str(evidence.environment_version_id or "")},
            "capabilities": evidence.capabilities,
        }
        if evidence is not None
        else None
    )
    rows = capability_statuses(evidence.catalogue if evidence is not None else [], document, current)
    selection = affected_scenarios(rows, evidence.scenario_requirements if evidence is not None else {})
    return {
        "environment_version_id": current["environment_version_id"] or None,
        "catalog_version": evidence.catalog_version if evidence is not None else None,
        "checked_at": evidence.captured_at.isoformat() if evidence is not None else None,
        "capabilities": rows,
        "selection": selection,
        "rules_version": RULES_VERSION,
        "computed_at": timezone.now().isoformat(),
    }
