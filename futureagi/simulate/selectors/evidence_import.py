"""Reads for an evidence import: which Observe project an environment may take
evidence from, and the bundle a person reviews before a test is drafted from it.

The bundle is read live from Observe on every view, cut to the import's size
budget. It says what each source holds and lacks, and what redaction would
replace before any of it becomes scenario text or seed data.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import cast
from uuid import UUID

from django.db.models import Q, QuerySet

from simulate.models import EvidenceImport, EvidenceImportCase, HostedHarnessJob
from tracer.models.project import Project
from tracer.selectors.observe_evidence import (
    Label,
    SourceEvidence,
    SourceKind,
    ToolCall,
    Turn,
    observe_evidence,
)
from tracer.utils.pii_scrubber import (
    PiiFinding,
    cut_clear,
    find_pii,
    find_pii_basic,
    redact,
)

# Sources one import may hold.
MAX_SOURCES = 20
# Characters of conversation text and tool inputs and outputs one import
# carries, shared equally by its sources. Redaction analyses every one of them
# on each read: about 45 microseconds a character on the shared dev box
# (8 vCPU, batched), so this keeps a read near 2.6 s.
MAX_IMPORT_CHARS = 60_000
# Findings one bundle lists; the rest are counted.
MAX_FINDINGS = 1000
# The longest line of the evidence document a builder reads.
DOCUMENT_LINE_CHARS = 1000

STATE_MISSING = (
    "Observe doesn't record the seed records the agent's tools read before the "
    "conversation. The draft labels what it assumes synthetic; add the real "
    "records to the scenario after drafting."
)
BASIC_REDACTION = (
    "Names and addresses were not checked. Emails, phone numbers, card and ID "
    "numbers, IP addresses and keys were redacted."
)
SOURCE_UNAVAILABLE = "The source was deleted or is no longer kept by Observe."
SOURCE_NOT_FOUND = "No retained session or call with this id in the project."
CUT_TO_FIT = "Cut to fit the import's size; the start is kept."


# The bundle a person reviews, as EvidenceImportSerializer writes it.


@dataclass(frozen=True, kw_only=True)
class EvidencePart:
    """What one part of a source holds (present, partial, truncated, missing or
    unreadable), and why when it is not all there."""

    status: str
    reason: str = ""


@dataclass(frozen=True, kw_only=True)
class TranscriptPart(EvidencePart):
    turns: tuple[Turn, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ToolCallsPart(EvidencePart):
    calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, kw_only=True)
class LabelsPart(EvidencePart):
    labels: tuple[Label, ...] = ()


@dataclass(frozen=True)
class ToolParts:
    tool_calls: ToolCallsPart
    tool_inputs: EvidencePart
    tool_outputs: EvidencePart


@dataclass(frozen=True)
class NewCheck:
    name: str
    what: str


@dataclass(frozen=True)
class CaseBundle:
    id: UUID
    source_id: str
    status: str
    reason: str
    source_available: bool
    scenario_key: str | None
    scenario_name: str
    new_checks: tuple[NewCheck, ...]
    changed_since_import: bool | None
    trace_ids: tuple[str, ...]
    started_at: datetime | None
    ended_at: datetime | None
    transcript: TranscriptPart
    tool_calls: ToolCallsPart
    tool_inputs: EvidencePart
    tool_outputs: EvidencePart
    starting_state: EvidencePart
    labels: LabelsPart


@dataclass(frozen=True)
class RedactionFinding:
    case_id: UUID
    part: str
    index: int
    field: str
    entity: str
    start: int
    end: int
    value: str
    replacement: str


@dataclass(frozen=True)
class Redaction:
    method: str
    reason: str
    findings: tuple[RedactionFinding, ...]
    total: int


@dataclass(frozen=True)
class ImportBundle:
    id: UUID
    environment_id: UUID
    project_id: UUID | None
    agent_version_id: UUID | None
    source_kind: str
    created_at: datetime
    drafting: bool
    simulated: bool
    cases: tuple[CaseBundle, ...]
    redaction: Redaction


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


def is_simulated(evidence_import: EvidenceImport) -> bool:
    """Whether the import's sources come from a simulation agent's own project:
    our simulated conversations, never production evidence. The Simulator source
    alone does not say so: a voice provider's project (Vapi, Retell) carries it
    and holds the agent's real calls."""
    return evidence_import.project.simulation_agent_id is not None


def environment_evidence_import(
    environment: HostedHarnessJob, import_id
) -> EvidenceImport | None:
    return (
        EvidenceImport.no_workspace_objects.filter(
            environment=environment, id=import_id, deleted=False
        )
        .select_related("project")
        .prefetch_related("cases")
        .first()
    )


def import_evidence(
    project: Project, source_kind: SourceKind, source_ids: list[str]
) -> dict[str, SourceEvidence]:
    """What Observe holds now for ``source_ids``, each source cut to its equal
    share of the import's budget. Nothing, once the project is deleted."""
    if project.deleted:
        return {}
    share = MAX_IMPORT_CHARS // max(1, len(source_ids))
    return {
        source_id: _within(evidence, share)
        for source_id, evidence in observe_evidence(
            project, source_kind, source_ids
        ).items()
    }


def evidence_digest(evidence: SourceEvidence) -> str:
    """A fingerprint of what a source holds, to tell later whether it changed;
    blank when part of it could not be read, since then nobody can tell."""
    if evidence.tool_calls is None or evidence.transcript_unreadable:
        return ""
    body = json.dumps(asdict(evidence), sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def import_bundle(
    evidence_import: EvidenceImport,
    evidence: dict[str, SourceEvidence] | None = None,
) -> ImportBundle:
    """The import as the review shows it. ``evidence`` is what Observe holds
    now for its sources (``import_evidence``); it is read when not given."""
    cases = list(evidence_import.cases.all())
    if evidence is None:
        evidence = import_evidence(
            evidence_import.project,
            _observe_kind(evidence_import),
            [case.source_id for case in cases],
        )
    # A source the import found nothing for stays unavailable: it was frozen so.
    current = {
        case.source_id: evidence.get(case.source_id)
        for case in cases
        if case.status != EvidenceImportCase.Status.UNAVAILABLE
    }
    return ImportBundle(
        id=evidence_import.id,
        environment_id=evidence_import.environment_id,
        project_id=evidence_import.project_id,
        agent_version_id=evidence_import.agent_version_id,
        source_kind=evidence_import.source_kind,
        created_at=evidence_import.created_at,
        drafting=evidence_import.drafting_job_id is not None,
        simulated=is_simulated(evidence_import),
        cases=tuple(_case(case, current.get(case.source_id)) for case in cases),
        redaction=_redaction(cases, current),
    )


def _within(evidence: SourceEvidence, chars: int) -> SourceEvidence:
    """``evidence`` with at most ``chars`` characters of text redaction must
    read: the conversation, the tool calls' inputs, outputs and errors, and the
    labels' notes. The parts share ``chars`` evenly, a part needing less giving
    the rest to the others. A cut keeps the start and says so."""
    sizes = (
        sum(len(turn.text) for turn in evidence.turns),
        sum(_call_size(call) for call in evidence.tool_calls or ()),
        sum(_label_size(label) for label in evidence.labels),
    )
    if sum(sizes) <= chars:
        return evidence
    turns_room, calls_room, labels_room = _shares(sizes, chars)
    turns, turns_cut, omitted = _cut_turns(evidence.turns, turns_room)
    calls, calls_cut = _cut_calls(evidence.tool_calls, calls_room)
    labels, labels_cut = _cut_labels(evidence.labels, labels_room)
    return replace(
        evidence,
        turns=turns,
        turns_truncated=evidence.turns_truncated or turns_cut,
        turns_omitted=evidence.turns_omitted + omitted,
        tool_calls=calls,
        tool_calls_truncated=evidence.tool_calls_truncated or calls_cut,
        labels=labels,
        labels_truncated=evidence.labels_truncated or labels_cut,
    )


def _call_size(call: ToolCall) -> int:
    return len(call.input) + len(call.output) + len(call.error)


def _label_size(label: Label) -> int:
    return len(label.notes) + (len(label.value) if isinstance(label.value, str) else 0)


def _shares(sizes: tuple[int, ...], total: int) -> list[int]:
    """``total`` split evenly across parts, none given more than its size."""
    shares = [0] * len(sizes)
    left = total
    for done, index in enumerate(sorted(range(len(sizes)), key=lambda i: sizes[i])):
        shares[index] = min(sizes[index], left // (len(sizes) - done))
        left -= shares[index]
    return shares


def _clip(text: str, room: int) -> tuple[str, int]:
    """``text`` cut to ``room``, never through a value redaction replaces, and
    the room left after it."""
    kept = cut_clear(text, max(0, room))
    return kept, room - len(kept)


def _cut_turns(
    turns: tuple[Turn, ...], room: int
) -> tuple[tuple[Turn, ...], bool, int]:
    """``turns`` in ``room`` characters: the last turns, then the first, and
    how many middle turns were left out. The last turn always stays (cut when
    it alone is too long), since a failure often comes at the end."""
    if sum(len(turn.text) for turn in turns) <= room:
        return turns, False, 0
    tail_room = room // 2
    tail: list[Turn] = []
    end = len(turns)
    while end > 0 and tail_room > 0:
        turn = turns[end - 1]
        if len(turn.text) > tail_room:
            if not tail:
                text, _ = _clip(turn.text, tail_room)
                tail.append(replace(turn, text=text, truncated=True))
                tail_room, end = 0, end - 1
            break
        tail.append(turn)
        tail_room -= len(turn.text)
        end -= 1
    head_room = room - room // 2 + tail_room
    head: list[Turn] = []
    start = 0
    while start < end and head_room > 0:
        turn = turns[start]
        text, head_room = _clip(turn.text, head_room)
        head.append(
            replace(turn, text=text, truncated=turn.truncated or text != turn.text)
        )
        start += 1
    return tuple(head + tail[::-1]), True, end - start


def _cut_calls(
    calls: tuple[ToolCall, ...] | None, room: int
) -> tuple[tuple[ToolCall, ...] | None, bool]:
    """``calls`` in ``room`` characters, failed calls given room first, then
    shown in the order they ran."""
    if calls is None:
        return None, False
    kept: dict[int, ToolCall] = {}
    cut = False
    for index in sorted(range(len(calls)), key=lambda i: (not calls[i].failed, i)):
        call = calls[index]
        if room <= 0:
            cut = True
            break
        given, room = _clip(call.input, room)
        taken, room = _clip(call.output, room)
        error, room = _clip(call.error, room)
        cut = cut or (given, taken, error) != (call.input, call.output, call.error)
        kept[index] = replace(
            call,
            input=given,
            output=taken,
            error=error,
            input_truncated=call.input_truncated or given != call.input,
            output_truncated=call.output_truncated or taken != call.output,
        )
    return tuple(kept[index] for index in sorted(kept)), cut


def _cut_labels(labels: tuple[Label, ...], room: int) -> tuple[tuple[Label, ...], bool]:
    kept: list[Label] = []
    cut = False
    for label in labels:
        if room <= 0 and _label_size(label):
            return tuple(kept), True
        notes, room = _clip(label.notes, room)
        value = label.value
        if isinstance(value, str):
            value, room = _clip(value, room)
        cut = cut or notes != label.notes or value != label.value
        kept.append(replace(label, notes=notes, value=value))
    return tuple(kept), cut


def _case(case: EvidenceImportCase, evidence: SourceEvidence | None) -> CaseBundle:
    if evidence is None:
        # A drafted, blocked or added case keeps what became of it; only one
        # still waiting reads as unavailable. Its evidence is gone either way.
        pending = case.status == EvidenceImportCase.Status.PENDING
        reason = (
            case.reason
            if case.status == EvidenceImportCase.Status.UNAVAILABLE
            else SOURCE_UNAVAILABLE
        )
        return CaseBundle(
            id=case.id,
            source_id=case.source_id,
            status=EvidenceImportCase.Status.UNAVAILABLE if pending else case.status,
            reason=reason if pending or not case.reason else case.reason,
            source_available=False,
            scenario_key=case.scenario_key,
            scenario_name=case.scenario_name,
            new_checks=_new_checks(case),
            changed_since_import=None,
            trace_ids=(),
            started_at=None,
            ended_at=None,
            transcript=TranscriptPart(status="missing", reason=reason),
            tool_calls=ToolCallsPart(status="missing", reason=reason),
            tool_inputs=EvidencePart(status="missing", reason=reason),
            tool_outputs=EvidencePart(status="missing", reason=reason),
            starting_state=EvidencePart(status="missing", reason=STATE_MISSING),
            labels=LabelsPart(status="missing", reason=reason),
        )
    digest = evidence_digest(evidence)
    tools = _tools(evidence)
    return CaseBundle(
        id=case.id,
        source_id=case.source_id,
        status=case.status,
        reason=case.reason,
        source_available=True,
        scenario_key=case.scenario_key,
        scenario_name=case.scenario_name,
        new_checks=_new_checks(case),
        # Unknown when either side could not be read in full.
        changed_since_import=(
            digest != case.digest if digest and case.digest else None
        ),
        trace_ids=tuple(evidence.trace_ids),
        started_at=evidence.started_at,
        ended_at=evidence.ended_at,
        transcript=_transcript(evidence),
        tool_calls=tools.tool_calls,
        tool_inputs=tools.tool_inputs,
        tool_outputs=tools.tool_outputs,
        starting_state=EvidencePart(status="missing", reason=STATE_MISSING),
        labels=_labels(evidence),
    )


def _new_checks(case: EvidenceImportCase) -> tuple[NewCheck, ...]:
    return tuple(
        NewCheck(name=str(one.get("name") or ""), what=str(one.get("what") or ""))
        for one in case.new_sub_goals or []
    )


def _transcript(evidence: SourceEvidence) -> TranscriptPart:
    turns = evidence.turns
    if evidence.transcript_unreadable and not turns:
        return TranscriptPart(
            status="unreadable", reason="The recorded transcript could not be read."
        )
    if not turns:
        return TranscriptPart(
            status="missing", reason="No conversation text was recorded."
        )
    if evidence.turns_omitted:
        count = evidence.turns_omitted
        return TranscriptPart(
            status="truncated",
            reason=f"{count} middle turn{'s' if count != 1 else ''} left out to fit; "
            "the first and last are kept.",
            turns=turns,
        )
    if evidence.turns_truncated or any(turn.truncated for turn in turns):
        return TranscriptPart(status="truncated", reason=CUT_TO_FIT, turns=turns)
    return TranscriptPart(status="present", turns=turns)


def _tools(evidence: SourceEvidence) -> ToolParts:
    if evidence.tool_calls is None:
        return _tools_as("unreadable", "The tool calls could not be read.")
    calls = evidence.tool_calls
    if not calls and evidence.tool_calls_truncated:
        return _tools_as("truncated", "More steps were recorded than could be shown.")
    if not calls:
        # Observe cannot tell an agent that called no tool from one whose
        # tools are not traced.
        return _tools_as(
            "missing",
            "No tool calls were recorded, or the agent's tools are not traced.",
        )
    listed = (
        ToolCallsPart(
            status="truncated",
            reason="More tool calls were recorded than are shown.",
            calls=calls,
        )
        if evidence.tool_calls_truncated
        else ToolCallsPart(status="present", calls=calls)
    )
    return ToolParts(
        tool_calls=listed,
        tool_inputs=_values(calls, "input", "inputs"),
        tool_outputs=_values(calls, "output", "outputs"),
    )


def _tools_as(status: str, reason: str) -> ToolParts:
    """Every tool part the same: nothing listed, for the one ``reason``."""
    part = EvidencePart(status=status, reason=reason)
    return ToolParts(
        tool_calls=ToolCallsPart(status=status, reason=reason),
        tool_inputs=part,
        tool_outputs=part,
    )


def _values(calls: tuple[ToolCall, ...], field: str, plural: str) -> EvidencePart:
    recorded = [call for call in calls if getattr(call, field)]
    if not recorded:
        return EvidencePart(
            status="missing", reason=f"The tool calls recorded no {plural}."
        )
    if len(recorded) < len(calls):
        return EvidencePart(
            status="partial", reason=f"Some tool calls recorded no {plural}."
        )
    if any(getattr(call, f"{field}_truncated") for call in calls):
        return EvidencePart(status="truncated", reason=CUT_TO_FIT)
    return EvidencePart(status="present")


def _labels(evidence: SourceEvidence) -> LabelsPart:
    labels = evidence.labels
    if not labels:
        return LabelsPart(status="missing", reason="Nobody labelled this source.")
    if evidence.labels_truncated:
        return LabelsPart(status="truncated", reason=CUT_TO_FIT, labels=labels)
    return LabelsPart(status="present", labels=labels)


def _observe_kind(evidence_import: EvidenceImport) -> SourceKind:
    # The model stores the kind as text; its choices are exactly Observe's kinds.
    return cast(SourceKind, evidence_import.source_kind)


def _texts(
    cases: list[EvidenceImportCase], evidence: Mapping[str, SourceEvidence | None]
) -> list[tuple[EvidenceImportCase, str, int, str, str]]:
    """Every text the bundle carries, as (case, part, index, field, text)."""
    values: list[tuple[EvidenceImportCase, str, int, str, str]] = []
    for case in cases:
        source = evidence.get(case.source_id)
        if source is None:
            continue
        values += [
            (case, "transcript", i, "text", t.text) for i, t in enumerate(source.turns)
        ]
        for i, call in enumerate(source.tool_calls or ()):
            values += [
                (case, "tool_calls", i, field, text)
                for field, text in (
                    ("input", call.input),
                    ("output", call.output),
                    ("error", call.error),
                )
            ]
        for i, label in enumerate(source.labels):
            values += [
                (case, "labels", i, "name", label.name),
                (case, "labels", i, "value", _label_text(label.value)),
                (case, "labels", i, "notes", label.notes),
            ]
    return values


def _label_text(value: object) -> str:
    """A label's value as the document shows it: text as written, anything
    else (a score, a choice list, a structured answer) as its JSON."""
    return value if isinstance(value, str) else json.dumps(value)


def _analysed(texts: list[str]) -> tuple[list[list[PiiFinding]], str, str]:
    """Each text's findings, analysed in one batch, with the method and its
    reason. Without Presidio, a fixed-shape match stands in."""
    found = find_pii(texts)
    if found is None:
        return find_pii_basic(texts), "basic", BASIC_REDACTION
    return found, "presidio", ""


def _redaction(
    cases: list[EvidenceImportCase], evidence: dict[str, SourceEvidence | None]
) -> Redaction:
    """What redaction would replace in every text the bundle carries."""
    values = _texts(cases, evidence)
    found, method, reason = _analysed([text for *_, text in values])
    findings = [
        RedactionFinding(
            case_id=case.id,
            part=part,
            index=index,
            field=field,
            entity=finding.entity,
            start=finding.start,
            end=finding.end,
            value=finding.value,
            replacement=finding.replacement,
        )
        for (case, part, index, field, _), hits in zip(values, found, strict=True)
        for finding in hits
    ]
    return Redaction(
        method=method,
        reason=reason,
        findings=tuple(findings[:MAX_FINDINGS]),
        total=len(findings),
    )


def evidence_document(
    evidence_import: EvidenceImport,
    case_ids: list,
    evidence: dict[str, SourceEvidence] | None = None,
) -> str:
    """The evidence a builder turn reads for ``case_ids``, every value redacted
    (all findings, not only the listed ones), one value per line and no line
    longer than DOCUMENT_LINE_CHARS, since a builder's Read cuts long lines.
    A case with nothing to read is left out."""
    wanted = {str(case_id) for case_id in case_ids}
    cases = [
        case
        for case in evidence_import.cases.all()
        if str(case.id) in wanted
        and case.status != EvidenceImportCase.Status.UNAVAILABLE
    ]
    if evidence is None:
        evidence = import_evidence(
            evidence_import.project,
            _observe_kind(evidence_import),
            [case.source_id for case in cases],
        )
    values = _texts(cases, evidence)
    found, method, reason = _analysed([text for *_, text in values])
    redacted = {
        (case.id, part, index, field): redact(text, hits)
        for (case, part, index, field, text), hits in zip(values, found, strict=True)
    }
    kind = evidence_import.source_kind
    lines = [
        f"# Evidence import {evidence_import.id}: {kind}s "
        f"from Observe project {evidence_import.project_id}.",
        (
            f"# These {kind}s come from a Simulator project: simulated, not "
            "production. Never write a case as something a customer did."
            if is_simulated(evidence_import)
            else f"# These are production {kind}s: each case is regression evidence."
        ),
        "# Personal data is replaced with tokens such as <EMAIL_ADDRESS>."
        + (f" {reason}" if reason else ""),
        "# Observe never records the starting state: anything you assume about "
        "it is synthetic and must be labelled so.",
        "# Labels are what annotators said: input evidence, not the expected answer.",
    ]
    for case in cases:
        source = evidence.get(case.source_id)
        if source is None:
            continue
        lines += [
            "",
            f"== case {case.id} ({evidence_import.source_kind} {case.source_id}, "
            f"traces {', '.join(source.trace_ids)}, "
            f"{source.started_at.isoformat()} to {source.ended_at.isoformat()})",
            f"-- transcript: {_transcript(source).status}",
        ]
        for i, turn in enumerate(source.turns):
            lines += _wrapped(
                f"[turn {i + 1}] {turn.role}",
                redacted[(case.id, "transcript", i, "text")],
            )
        lines.append(f"-- tool calls: {_tools(source).tool_calls.status}")
        for i, call in enumerate(source.tool_calls or ()):
            lines.append(
                f"[call {i + 1}] {call.name} at {call.at.isoformat()}"
                + (" FAILED" if call.failed else "")
            )
            for field in ("input", "output", "error"):
                text = redacted[(case.id, "tool_calls", i, field)]
                if text:
                    lines += _wrapped(f"[call {i + 1}] {field}", text)
        lines.append(f"-- labels: {_labels(source).status}")
        for i, label in enumerate(source.labels):
            name = redacted[(case.id, "labels", i, "name")]
            lines += _wrapped(
                f"[label {i + 1}] {name} on the {label.target}",
                redacted[(case.id, "labels", i, "value")],
            )
            notes = redacted[(case.id, "labels", i, "notes")]
            if notes:
                lines += _wrapped(f"[label {i + 1}] notes", notes)
    return "\n".join(lines) + "\n"


def _wrapped(head: str, text: str) -> list[str]:
    """``head: text`` as lines of at most DOCUMENT_LINE_CHARS, newlines kept
    as separate lines, each continuation marked."""
    room = DOCUMENT_LINE_CHARS - len(head) - len(" (cont.): ")
    pieces = [
        line[start : start + room]
        for line in (text.splitlines() or [""])
        for start in range(0, max(len(line), 1), room)
    ]
    return [
        f"{head}{' (cont.)' if n else ''}: {piece}" for n, piece in enumerate(pieces)
    ]
