"""Reads for an evidence import: which Observe project an environment may take
evidence from, and the bundle a person reviews before a test is drafted from it.

The bundle is read live from Observe on every view. It says what each source
holds and lacks, and what redaction would replace before any of it becomes
scenario text or seed data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from django.db.models import Q, QuerySet

from simulate.models import EvidenceImport, EvidenceImportCase, HostedHarnessJob
from tracer.models.project import Project
from tracer.selectors.observe_evidence import SourceEvidence, observe_evidence
from tracer.utils.pii_scrubber import find_pii

# Sources one import may hold.
MAX_SOURCES = 20
# Findings one bundle lists; the rest are counted.
MAX_FINDINGS = 1000

STATE_MISSING = (
    "Observe does not record the records the agent's tools read before the "
    "conversation. Supply them, or label the starting state synthetic."
)
REDACTION_UNAVAILABLE = (
    "Automatic redaction is unavailable on this server (the pii extra, "
    "Presidio, is not installed or failed to load). Evidence cannot be turned "
    "into tests until it is."
)
SOURCE_UNAVAILABLE = "The source was deleted or is no longer kept by Observe."
SOURCE_NOT_FOUND = "No retained session or call with this id in the project."


def evidence_project(
    projects: QuerySet[Project], environment: HostedHarnessJob, project_id
) -> Project | None:
    """The project among ``projects`` (those the caller may read) that the
    environment may take evidence from: its organization, and a workspace the
    environment's own scope reads, as Observe scopes a default workspace. A
    project in another workspace is refused, so its production data never
    lands where that workspace's people cannot see it."""
    workspace = environment.workspace
    if workspace is not None and not workspace.is_default:
        scope = Q(workspace=workspace)
    else:
        scope = Q(workspace__isnull=True) | Q(workspace__is_default=True)
        if workspace is not None:
            scope |= Q(workspace=workspace)
    return (
        projects.filter(scope, organization_id=environment.organization_id)
        .filter(id=project_id)
        .first()
    )


def environment_evidence_import(
    environment: HostedHarnessJob, import_id
) -> EvidenceImport | None:
    return (
        EvidenceImport.no_workspace_objects.filter(
            environment=environment, id=import_id, deleted=False
        )
        .prefetch_related("cases")
        .first()
    )


def evidence_digest(evidence: SourceEvidence) -> str:
    """A fingerprint of what a source holds, to tell later whether it changed."""
    body = json.dumps(asdict(evidence), sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def import_bundle(
    evidence_import: EvidenceImport,
    evidence: dict[str, SourceEvidence] | None = None,
) -> dict:
    """The import as the review shows it. ``evidence`` is what Observe holds
    now for its sources; it is read when not given."""
    cases = list(evidence_import.cases.all())
    if evidence is None:
        evidence = observe_evidence(
            str(evidence_import.project_id),
            evidence_import.source_kind,
            [case.source_id for case in cases],
        )
    return {
        "id": evidence_import.id,
        "environment_id": evidence_import.environment_id,
        "project_id": evidence_import.project_id,
        "agent_version_id": evidence_import.agent_version_id,
        "source_kind": evidence_import.source_kind,
        "created_at": evidence_import.created_at,
        "cases": [_case(case, evidence.get(case.source_id)) for case in cases],
        "redaction": _redaction(cases, evidence),
    }


def _case(case: EvidenceImportCase, evidence: SourceEvidence | None) -> dict:
    if evidence is None:
        reason = case.reason
        if case.status != EvidenceImportCase.Status.UNAVAILABLE:
            reason = SOURCE_UNAVAILABLE
        return {
            "id": case.id,
            "source_id": case.source_id,
            "status": EvidenceImportCase.Status.UNAVAILABLE,
            "reason": reason,
            "changed_since_import": False,
            "trace_ids": [],
            "started_at": None,
            "ended_at": None,
            "transcript": _part("missing", reason, turns=[]),
            "tool_calls": _part("missing", reason, calls=[]),
            "tool_io": _part("missing", reason),
            "starting_state": _part("missing", STATE_MISSING),
            "labels": _part("missing", reason, labels=[]),
        }
    return {
        "id": case.id,
        "source_id": case.source_id,
        "status": case.status,
        "reason": case.reason,
        "changed_since_import": evidence_digest(evidence) != case.digest,
        "trace_ids": list(evidence.trace_ids),
        "started_at": evidence.started_at,
        "ended_at": evidence.ended_at,
        "transcript": _transcript(evidence),
        **_tools(evidence),
        "starting_state": _part("missing", STATE_MISSING),
        "labels": _labels(evidence),
    }


def _part(status: str, reason: str = "", **items) -> dict:
    return {"status": status, "reason": reason, **items}


def _transcript(evidence: SourceEvidence) -> dict:
    turns = [asdict(turn) for turn in evidence.turns]
    if not turns:
        return _part("missing", "No conversation text was recorded.", turns=[])
    if evidence.turns_truncated or any(t["truncated"] for t in turns):
        return _part(
            "truncated", "Cut to fit; the start of each part is kept.", turns=turns
        )
    return _part("present", turns=turns)


def _tools(evidence: SourceEvidence) -> dict:
    if evidence.tool_calls is None:
        missing = _part("missing", "The tool calls could not be read.")
        return {"tool_calls": {**missing, "calls": []}, "tool_io": missing}
    calls = [asdict(call) for call in evidence.tool_calls]
    if not calls:
        # Observe cannot tell an agent that called no tool from one whose
        # tools are not traced.
        missing = _part(
            "missing",
            "No tool calls were recorded, or the agent's tools are not traced.",
        )
        return {"tool_calls": {**missing, "calls": []}, "tool_io": missing}
    listed = (
        _part("truncated", "More tool calls were recorded than are shown.", calls=calls)
        if evidence.tool_calls_truncated
        else _part("present", calls=calls)
    )
    with_io = [c for c in calls if c["input"] or c["output"]]
    if not with_io:
        io = _part("missing", "The tool calls recorded no inputs or outputs.")
    elif len(with_io) < len(calls):
        io = _part("partial", "Some tool calls recorded no input or output.")
    elif any(c["input_truncated"] or c["output_truncated"] for c in calls):
        io = _part("truncated", "Cut to fit; the start of each value is kept.")
    else:
        io = _part("present")
    return {"tool_calls": listed, "tool_io": io}


def _labels(evidence: SourceEvidence) -> dict:
    labels = [asdict(label) for label in evidence.labels]
    if not labels:
        return _part("missing", "Nobody labelled this source.", labels=[])
    return _part("present", labels=labels)


def _redaction(
    cases: list[EvidenceImportCase], evidence: dict[str, SourceEvidence]
) -> dict:
    """What redaction would replace, per value. It fails closed: when nothing
    could look, the status says so and no finding claims the text is clean."""
    findings: list[dict] = []
    total = 0
    for case in cases:
        source = evidence.get(case.source_id)
        if source is None:
            continue
        values = [("transcript", i, "text", t.text) for i, t in enumerate(source.turns)]
        for i, call in enumerate(source.tool_calls or ()):
            values += [("tool_calls", i, "input", call.input)]
            values += [("tool_calls", i, "output", call.output)]
        for part, index, field, text in values:
            found = find_pii(text)
            if found is None:
                return {
                    "status": "unavailable",
                    "reason": REDACTION_UNAVAILABLE,
                    "findings": [],
                    "total": 0,
                }
            total += len(found)
            findings += [
                {
                    "case_id": case.id,
                    "part": part,
                    "index": index,
                    "field": field,
                    **asdict(finding),
                }
                for finding in found
            ][: max(0, MAX_FINDINGS - len(findings))]
    return {"status": "ready", "reason": "", "findings": findings, "total": total}
