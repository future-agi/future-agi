"""Reads for an evidence import: which Observe project or dataset an
environment may take evidence from, and the bundle a person reviews before a
test is drafted from it.

An Observe or dataset bundle is read live on every view, cut to the import's
size budget. A transcript's is its stored text, cut and redacted when it was
added. The bundle says what each source holds and lacks, and what redaction
replaces before any of it becomes scenario text or seed data.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from django.db.models import Count, OuterRef, Q, QuerySet, Subquery
from django.db.models.fields.json import KeyTextTransform

from model_hub.models.develop_dataset import Dataset
from simulate.models import EvidenceImport, EvidenceImportCase, HostedHarnessJob
from simulate.pydantic_schemas.agent_fix import FixEvidence
from simulate.pydantic_schemas.evidence_import import (
    DatasetColumns,
    ImportKind,
    RedactionMethod,
    StoredRedaction,
    StoredTranscript,
    StoredTurn,
)
from simulate.selectors.dataset_evidence import dataset_evidence, text_evidence
from simulate.utils.transcript_parse import ParsedTranscript
from tracer.models.project import Project, ProjectSourceChoices
from tracer.selectors.observe_evidence import (
    Label,
    SourceEvidence,
    SourceKind,
    ToolCall,
    Turn,
    observe_evidence,
)
from tracer.utils.pii_scrubber import PiiFinding, find_pii, find_pii_basic, redact

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
# Unfinished imports an environment lists, newest first.
MAX_OPEN_IMPORTS = 50
# Cases a person still has to act on: draft, add, or retry by importing again
# (a blocked case). An import stays listed while it has one.
OPEN_CASE_STATUSES = (
    EvidenceImportCase.Status.PENDING,
    EvidenceImportCase.Status.DRAFTED,
    EvidenceImportCase.Status.BLOCKED,
)

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
TRANSCRIPT_NO_TOOLS = "A transcript carries no tool calls."
TRANSCRIPT_NO_LABELS = "A transcript carries no labels."
TRANSCRIPT_STATE_MISSING = (
    "A transcript doesn't hold the seed records the agent's tools read before the "
    "conversation. The draft labels what it assumes synthetic; add the real "
    "records to the scenario after drafting."
)
ROW_UNAVAILABLE = "The row or its dataset was deleted."
ROW_NOT_FOUND = "No row with this id in the dataset."
ROW_NO_TOOLS = "A dataset row carries no tool calls."
ROW_NO_LABELS = "No label column was picked, or this row's label cells are empty."
ROW_STATE_MISSING = (
    "A dataset row doesn't hold the seed records the agent's tools read before the "
    "conversation. The draft labels what it assumes synthetic; add the real "
    "records to the scenario after drafting."
)
NO_EXPECTED = "Only a dataset's expected outcome column records one."
ROW_NO_EXPECTED = "No column was picked as the expected outcome, or it was deleted."
ROW_EXPECTED_EMPTY = "This row's expected outcome is empty."


def _environment_scope(environment: HostedHarnessJob) -> Q:
    """What the environment may take evidence from: its organization, and a
    workspace the environment's own scope reads, as Observe scopes a default
    workspace. Anything in another workspace is refused, so its data never
    lands where that workspace's people cannot see it."""
    workspace = environment.workspace
    if workspace is not None and not workspace.is_default:
        scope = Q(workspace=workspace)
    else:
        scope = Q(workspace__isnull=True) | Q(workspace__is_default=True)
        if workspace is not None:
            scope |= Q(workspace=workspace)
    return scope & Q(organization_id=environment.organization_id)


def evidence_project(
    projects: QuerySet[Project], environment: HostedHarnessJob, project_id
) -> Project | None:
    """The project among ``projects`` (those the caller may read) that the
    environment may take evidence from."""
    return projects.filter(_environment_scope(environment), id=project_id).first()


def evidence_dataset(
    datasets: QuerySet[Dataset], environment: HostedHarnessJob, dataset_id
) -> Dataset | None:
    """The dataset among ``datasets`` (those the caller may read) that the
    environment may take evidence from."""
    return datasets.filter(_environment_scope(environment), id=dataset_id).first()


def is_transcript(evidence_import: EvidenceImport) -> bool:
    """Whether the import's text is stored: it has no source to read again."""
    return evidence_import.source_kind == EvidenceImport.SourceKind.TRANSCRIPT


def is_dataset(evidence_import: EvidenceImport) -> bool:
    return evidence_import.source_kind == EvidenceImport.SourceKind.DATASET


def source_noun(source_kind: str) -> str:
    """What one source of ``source_kind`` is called to the builder."""
    if source_kind == EvidenceImport.SourceKind.DATASET:
        return "dataset row"
    return source_kind


def is_declared(evidence_import: EvidenceImport) -> bool:
    """Whether the person said where the evidence comes from: transcripts and
    dataset rows. An Observe import's provenance is its project's."""
    return is_transcript(evidence_import) or is_dataset(evidence_import)


def is_simulated(evidence_import: EvidenceImport) -> bool:
    """Whether the import's sources come from a Simulator project: our own
    simulated conversations, never production evidence."""
    project = evidence_import.project
    return (
        project is not None and project.source == ProjectSourceChoices.SIMULATOR.value
    )


def is_production(evidence_import: EvidenceImport) -> bool:
    """Whether the import is production evidence: an Observe project's that is
    not a Simulator project, or transcripts or dataset rows the person said
    come from production."""
    if is_declared(evidence_import):
        return evidence_import.declared_production
    return not is_simulated(evidence_import)


def environment_evidence_import(
    environment: HostedHarnessJob, import_id
) -> EvidenceImport | None:
    return (
        EvidenceImport.no_workspace_objects.filter(
            environment=environment, id=import_id, deleted=False
        )
        .select_related("project", "dataset")
        .prefetch_related("cases")
        .first()
    )


OpenStatus = Literal["drafting", "blocked", "partly_added", "drafted", "waiting"]


@dataclass(frozen=True)
class OpenEvidenceImport:
    """One import a person can still finish, as the Draft imports list shows it."""

    id: UUID
    source_kind: str
    # The project's or dataset's name, else the first transcript's (redacted).
    source_name: str | None
    # A transcript's text is stored, so it never goes missing.
    source_available: bool
    case_count: int
    added_count: int
    status: OpenStatus
    created_at: datetime
    can_discard: bool


def open_evidence_imports(environment: HostedHarnessJob) -> list[OpenEvidenceImport]:
    """The environment's imports a person can still finish, newest first: those
    with a case to draft, add or retry, or being drafted. Read from what the
    import stores only: no source is read again and nothing is redacted."""
    imports = (
        EvidenceImport.no_workspace_objects.filter(
            environment=environment, deleted=False
        )
        .annotate(
            case_count=Count("cases"),
            open_count=Count("cases", filter=Q(cases__status__in=OPEN_CASE_STATUSES)),
            added_count=Count(
                "cases", filter=Q(cases__status=EvidenceImportCase.Status.ADDED)
            ),
            drafted_count=Count(
                "cases", filter=Q(cases__status=EvidenceImportCase.Status.DRAFTED)
            ),
            pending_count=Count(
                "cases", filter=Q(cases__status=EvidenceImportCase.Status.PENDING)
            ),
            # The first transcript's name, as the person gave it (redacted).
            transcript_name=Subquery(
                EvidenceImportCase.no_workspace_objects.filter(
                    evidence_import=OuterRef("pk")
                )
                .order_by("position")
                .annotate(given=KeyTextTransform("name", "transcript"))
                .values("given")[:1]
            ),
        )
        .filter(Q(open_count__gt=0) | Q(drafting_job__isnull=False))
        .select_related("project", "dataset")
        .order_by("-created_at")[:MAX_OPEN_IMPORTS]
    )
    return [
        OpenEvidenceImport(
            id=one.id,
            source_kind=one.source_kind,
            source_name=source.name
            if (source := one.project or one.dataset)
            else one.transcript_name or None,
            source_available=source is None or not source.deleted,
            case_count=one.case_count,
            added_count=one.added_count,
            status=_open_status(one),
            created_at=one.created_at,
            can_discard=one.drafting_job_id is None,
        )
        for one in imports
    ]


def _open_status(evidence_import) -> OpenStatus:
    if evidence_import.drafting_job_id is not None:
        return "drafting"
    # Listed, yet nothing to draft or add: only blocked cases are left.
    if not evidence_import.pending_count and not evidence_import.drafted_count:
        return "blocked"
    if evidence_import.added_count:
        return "partly_added"
    if evidence_import.drafted_count:
        return "drafted"
    return "waiting"


def evidence_origins(environment: HostedHarnessJob, scenario_keys) -> list[FixEvidence]:
    """The evidence imports of ``environment`` that added any of ``scenario_keys`` to its
    suite, oldest first, each with those cases' sources in import order. A case
    another import repeated counts that import's source too, under the scenario
    it became. A scenario no import added has no origin."""
    added = Q(
        status=EvidenceImportCase.Status.ADDED,
        scenario_key__in=list(scenario_keys),
    )
    repeated = Q(
        status=EvidenceImportCase.Status.DUPLICATE,
        duplicate_of__status=EvidenceImportCase.Status.ADDED,
        duplicate_of__scenario_key__in=list(scenario_keys),
    )
    cases = (
        EvidenceImportCase.no_workspace_objects.filter(
            added | repeated,
            evidence_import__environment=environment,
            evidence_import__deleted=False,
        )
        .select_related("evidence_import", "duplicate_of")
        .order_by("evidence_import__created_at", "position")
    )
    origins: dict[str, FixEvidence] = {}
    for case in cases:
        original = case.duplicate_of
        scenario_key = (
            original.scenario_key
            if case.status == EvidenceImportCase.Status.DUPLICATE and original
            else case.scenario_key
        )
        origin = origins.setdefault(
            str(case.evidence_import_id),
            FixEvidence(
                import_id=str(case.evidence_import_id),
                source_kind=_import_kind(case.evidence_import),
                source_ids=[],
                scenario_keys=[],
            ),
        )
        origin.source_ids.append(case.source_id)
        origin.scenario_keys.append(scenario_key)
    return list(origins.values())


def import_evidence(
    project: Project,
    source_kind: SourceKind,
    source_ids: list[str],
    *,
    shared_by: int | None = None,
) -> dict[str, SourceEvidence]:
    """What Observe holds now for ``source_ids``, each source cut to its equal
    share of the import's budget, split ``shared_by`` ways (by default, among
    ``source_ids``). Nothing, once the project is deleted."""
    if project.deleted:
        return {}
    return _shared(
        observe_evidence(project, source_kind, source_ids),
        shared_by or len(source_ids),
    )


def import_rows(
    dataset: Dataset,
    columns: DatasetColumns,
    row_ids: list[str],
    *,
    shared_by: int | None = None,
) -> dict[str, SourceEvidence]:
    """What ``dataset`` holds now for ``row_ids``, each row cut to its equal
    share of the import's budget, split as ``import_evidence`` splits it."""
    return _shared(
        dataset_evidence(dataset, columns, row_ids), shared_by or len(row_ids)
    )


def _shared(found: dict[str, SourceEvidence], ways: int) -> dict[str, SourceEvidence]:
    share = MAX_IMPORT_CHARS // max(1, ways)
    return {
        source_id: _within(evidence, share) for source_id, evidence in found.items()
    }


def import_columns(evidence_import: EvidenceImport) -> DatasetColumns | None:
    """The columns a dataset import reads its rows through."""
    if evidence_import.dataset_columns is None:
        return None
    return DatasetColumns.model_validate(evidence_import.dataset_columns)


def current_evidence(
    evidence_import: EvidenceImport, cases: list[EvidenceImportCase]
) -> dict[str, SourceEvidence]:
    """What each case's source holds now: Observe or the dataset read again,
    or a transcript's stored text."""
    if is_transcript(evidence_import):
        return {
            case.source_id: found
            for case in cases
            if (found := _stored_evidence(case)) is not None
        }
    source_ids = [case.source_id for case in cases]
    # Each source keeps the share it was cut to when the import was made: the
    # budget split among all the import's cases, not only those read now.
    shared_by = len(evidence_import.cases.all())
    if is_dataset(evidence_import):
        columns = import_columns(evidence_import)
        if evidence_import.dataset is None or columns is None:
            return {}
        return import_rows(
            evidence_import.dataset, columns, source_ids, shared_by=shared_by
        )
    if evidence_import.project is None:
        return {}
    return import_evidence(
        evidence_import.project,
        _observe_kind(evidence_import),
        source_ids,
        shared_by=shared_by,
    )


def stored_transcripts(
    named: list[tuple[str, ParsedTranscript]],
) -> tuple[list[StoredTranscript], StoredRedaction]:
    """Named transcripts as an import stores them: every value redaction finds, in
    the text and in the name, replaced with its token, then each cut to its equal
    share of the import's budget. Redacted whole first, so a cut can never leave
    part of a value (a card number split at one of its spaces) where redaction no
    longer recognises it. Nothing unredacted is returned."""
    share = MAX_IMPORT_CHARS // max(1, len(named))
    texts = [name for name, _ in named] + [
        turn.text for _, parsed in named for turn in parsed.turns
    ]
    found, method, reason = _analysed(texts)
    redacted = iter(
        [redact(text, hits) for text, hits in zip(texts, found, strict=True)]
    )
    names = [next(redacted) for _ in named]
    clean = [
        replace(
            parsed,
            turns=tuple(replace(turn, text=next(redacted)) for turn in parsed.turns),
        )
        for _, parsed in named
    ]
    cut = [_cut_transcript(parsed, share) for parsed in clean]
    stored = [
        StoredTranscript(
            name=name,
            turns=[
                StoredTurn(role=turn.role, text=turn.text, truncated=turn.truncated)
                for turn in source.turns
            ],
            turns_truncated=source.turns_truncated,
            turns_omitted=source.turns_omitted,
            left_out=parsed.left_out,
        )
        for name, source, parsed in zip(names, cut, clean, strict=True)
    ]
    total = sum(len(hits) for hits in found)
    return stored, StoredRedaction(method=method, reason=reason, total=total)


def _cut_transcript(parsed: ParsedTranscript, chars: int) -> SourceEvidence:
    """A parsed transcript cut as Observe evidence is: each turn to TURN_CHARS,
    MAX_TURNS turns (the first and last halves), then ``chars`` characters. A
    cut turn ends at its last whitespace, so no redaction token is left in two
    pieces."""
    cut = _within(text_evidence("", parsed), chars)
    return replace(
        cut,
        turns=tuple(
            replace(turn, text=_to_whitespace(turn.text)) if turn.truncated else turn
            for turn in cut.turns
        ),
    )


def _to_whitespace(text: str) -> str:
    """A cut text ended at its last whitespace; empty when it has none."""
    end = max(text.rfind(space) for space in " \t\r\n")
    return text[:end].rstrip() if end > 0 else ""


def _stored(case: EvidenceImportCase) -> StoredTranscript | None:
    return (
        StoredTranscript.model_validate(case.transcript)
        if case.transcript is not None
        else None
    )


def _stored_evidence(case: EvidenceImportCase) -> SourceEvidence | None:
    stored = _stored(case)
    if stored is None:
        return None
    return transcript_evidence(stored, case.source_id)


def transcript_evidence(stored: StoredTranscript, source_id: str) -> SourceEvidence:
    """A stored transcript as evidence: its turns, and no tool calls or labels."""
    return SourceEvidence(
        source_id=source_id,
        trace_ids=(),
        started_at=None,
        ended_at=None,
        turns=tuple(
            Turn(turn.role, turn.text, turn.truncated, "", None)
            for turn in stored.turns
        ),
        turns_truncated=stored.turns_truncated,
        turns_omitted=stored.turns_omitted,
        transcript_unreadable=False,
        tool_calls=(),
        tool_calls_truncated=False,
        labels=(),
    )


def evidence_digest(evidence: SourceEvidence) -> str:
    """A fingerprint of what a source holds, to tell later whether it changed;
    blank when part of it could not be read, since then nobody can tell."""
    if evidence.tool_calls is None or evidence.transcript_unreadable:
        return ""
    held = asdict(evidence)
    # A source with no expected outcome, as every Observe one, is fingerprinted as
    # it was before dataset rows brought one, so its stored fingerprint still holds.
    if held["expected"] is None and not held["expected_truncated"]:
        del held["expected"], held["expected_truncated"]
    body = json.dumps(held, sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def content_digest(evidence: SourceEvidence) -> str:
    """A fingerprint of the case a source holds: its turns, tool calls and expected
    outcome, without ids or times, so two sources holding the same conversation
    match. Taken on the evidence as the import cut it, in one form for every
    source kind: each text redacted and its whitespace collapsed, since a
    transcript is stored redacted while Observe and a dataset row are read raw.
    Blank when part of it could not be read or it holds no turn, since then
    nobody can tell."""
    if (
        evidence.tool_calls is None
        or evidence.transcript_unreadable
        or not evidence.turns
    ):
        return ""
    turns = [turn.text for turn in evidence.turns]
    calls = [
        _as_text(part)
        for call in evidence.tool_calls
        for part in (call.error or "", call.input, call.output)
    ]
    expected = [] if evidence.expected is None else [evidence.expected]
    texts = [" ".join(text.split()) for text in _redacted(turns + calls + expected)]
    said, done = texts[: len(turns)], texts[len(turns) : len(turns) + len(calls)]
    body = json.dumps(
        {
            "turns": [
                [turn.role, text]
                for turn, text in zip(evidence.turns, said, strict=True)
            ],
            "tool_calls": [
                [call.name, call.failed, *done[3 * n : 3 * n + 3]]
                for n, call in enumerate(evidence.tool_calls)
            ],
            "expected": texts[-1] if expected else None,
        },
        sort_keys=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _as_text(value: object) -> str:
    return (
        value
        if isinstance(value, str)
        else json.dumps(value, sort_keys=True, default=str)
    )


def _redacted(texts: list[str]) -> list[str]:
    """Each text with every value redaction finds replaced with its token."""
    if not texts:
        return []
    found, _method, _reason = _analysed(texts)
    return [redact(text, hits) for text, hits in zip(texts, found, strict=True)]


def import_bundle(
    evidence_import: EvidenceImport,
    evidence: dict[str, SourceEvidence] | None = None,
) -> dict:
    """The import as the review shows it. ``evidence`` is what its sources hold
    now (``current_evidence``); it is read when not given. A case repeating another
    of this import is left out: that one is proposed, and counts it as a source."""
    every = list(evidence_import.cases.all())
    own = {case.id for case in every}
    cases = [case for case in every if case.duplicate_of_id not in own]
    provenance = _provenance(cases)
    if evidence is None:
        evidence = current_evidence(evidence_import, cases)
    # A source the import found nothing for stays unavailable: it was frozen so.
    current = {
        case.source_id: evidence.get(case.source_id)
        for case in cases
        if case.status != EvidenceImportCase.Status.UNAVAILABLE
    }
    transcript = is_transcript(evidence_import)
    return {
        "id": evidence_import.id,
        "environment_id": evidence_import.environment_id,
        "project_id": evidence_import.project_id,
        "dataset_id": evidence_import.dataset_id,
        "agent_version_id": evidence_import.agent_version_id,
        "source_kind": evidence_import.source_kind,
        "created_at": evidence_import.created_at,
        "drafting": evidence_import.drafting_job_id is not None,
        "simulated": is_simulated(evidence_import),
        "production": is_production(evidence_import),
        "cases": [
            {
                **_case(
                    case,
                    current.get(case.source_id),
                    evidence_import.source_kind,
                    _stored(case) if transcript else None,
                    name=_row_name(evidence_import, case)
                    if is_dataset(evidence_import)
                    else "",
                ),
                **provenance(case),
            }
            for case in cases
        ],
        "redaction": (
            _stored_redaction(evidence_import)
            if transcript
            else _redaction(cases, current)
        ),
    }


def _provenance(cases: list[EvidenceImportCase]):
    """What the review says of each case's other sources: how many more sources a
    case has (its duplicates in any import still kept), and, for a case already
    imported elsewhere, the case it repeats, to open that import's review."""
    also = dict(
        EvidenceImportCase.no_workspace_objects.filter(
            duplicate_of__in=[case.id for case in cases],
            evidence_import__deleted=False,
        )
        .order_by()
        .values_list("duplicate_of_id")
        .annotate(count=Count("id"))
    )
    repeated = {
        one["id"]: one
        for one in EvidenceImportCase.no_workspace_objects.filter(
            id__in=[case.duplicate_of_id for case in cases if case.duplicate_of_id]
        ).values("id", "evidence_import_id", "scenario_name")
    }

    def of(case: EvidenceImportCase) -> dict:
        original = repeated.get(case.duplicate_of_id) if case.duplicate_of_id else None
        return {
            "also_from": also.get(case.id, 0),
            "duplicate_of": (
                {
                    "import_id": original["evidence_import_id"],
                    "case_id": original["id"],
                    "scenario_name": original["scenario_name"],
                }
                if original
                else None
            ),
        }

    return of


def _stored_redaction(evidence_import: EvidenceImport) -> dict:
    """What redaction replaced when the transcripts were stored. Their values
    were never kept, so none is listed."""
    stored = StoredRedaction.model_validate(evidence_import.redaction)
    return {
        "method": stored.method,
        "reason": stored.reason,
        "findings": [],
        "total": stored.total,
    }


def _within(evidence: SourceEvidence, chars: int) -> SourceEvidence:
    """``evidence`` with at most ``chars`` characters of text redaction must
    read: the conversation, the tool calls' inputs, outputs and errors, the
    labels' notes and the expected outcome. The parts share ``chars`` evenly, a
    part needing less giving the rest to the others. A cut keeps the start and
    says so."""
    sizes = (
        sum(len(turn.text) for turn in evidence.turns),
        sum(_call_size(call) for call in evidence.tool_calls or ()),
        sum(_label_size(label) for label in evidence.labels),
        len(evidence.expected or ""),
    )
    if sum(sizes) <= chars:
        return evidence
    turns_room, calls_room, labels_room, expected_room = _shares(sizes, chars)
    turns, turns_cut, omitted = _cut_turns(evidence.turns, turns_room)
    calls, calls_cut = _cut_calls(evidence.tool_calls, calls_room)
    labels, labels_cut = _cut_labels(evidence.labels, labels_room)
    expected = evidence.expected
    if expected is not None:
        expected, _ = _clip(expected, expected_room)
    return replace(
        evidence,
        turns=turns,
        turns_truncated=evidence.turns_truncated or turns_cut,
        turns_omitted=evidence.turns_omitted + omitted,
        tool_calls=calls,
        tool_calls_truncated=evidence.tool_calls_truncated or calls_cut,
        labels=labels,
        labels_truncated=evidence.labels_truncated or labels_cut,
        expected=expected,
        expected_truncated=evidence.expected_truncated or expected != evidence.expected,
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
    """``text`` cut to ``room``, and the room left after it."""
    kept = text[: max(0, room)]
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
                tail.append(replace(turn, text=turn.text[:tail_room], truncated=True))
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


def _row_name(evidence_import: EvidenceImport, case: EvidenceImportCase) -> str:
    """What a dataset row is called in the review: its dataset, since a row id
    says nothing to a person. Deleting the dataset deletes its imports."""
    dataset = evidence_import.dataset
    return f"Row from {dataset.name}" if dataset else ""


def _case(
    case: EvidenceImportCase,
    evidence: SourceEvidence | None,
    source_kind: str,
    stored: StoredTranscript | None = None,
    *,
    name: str = "",
) -> dict:
    """One case as the review shows it; ``stored`` is a transcript case's text and
    ``name`` what any other case is called (blank for an Observe source)."""
    row = source_kind == EvidenceImport.SourceKind.DATASET
    if evidence is None:
        # A drafted, blocked or added case keeps what became of it; only one
        # still waiting reads as unavailable. Its evidence is gone either way.
        pending = case.status == EvidenceImportCase.Status.PENDING
        reason = (
            case.reason
            if case.status == EvidenceImportCase.Status.UNAVAILABLE
            else ROW_UNAVAILABLE
            if row
            else SOURCE_UNAVAILABLE
        )
        return {
            "id": case.id,
            "source_id": case.source_id,
            "name": name,
            "status": EvidenceImportCase.Status.UNAVAILABLE if pending else case.status,
            "reason": reason if pending or not case.reason else case.reason,
            "source_available": False,
            "scenario_key": case.scenario_key,
            "scenario_name": case.scenario_name,
            "new_checks": _new_checks(case),
            "changed_since_import": None,
            "trace_ids": [],
            "started_at": None,
            "ended_at": None,
            "transcript": _part("missing", reason, turns=[]),
            "tool_calls": _part("missing", reason, calls=[]),
            "tool_inputs": _part("missing", reason),
            "tool_outputs": _part("missing", reason),
            "starting_state": _part("missing", STATE_MISSING),
            "labels": _part("missing", reason, labels=[]),
            "expected_outcome": _part("missing", reason, text=""),
        }
    digest = evidence_digest(evidence)
    shown = {
        "id": case.id,
        "source_id": case.source_id,
        "name": stored.name if stored else name,
        "status": case.status,
        "reason": case.reason,
        "source_available": True,
        "scenario_key": case.scenario_key,
        "scenario_name": case.scenario_name,
        "new_checks": _new_checks(case),
        # Unknown when either side could not be read in full. A transcript has
        # no source to read again, so it is never known.
        "changed_since_import": (
            digest != case.digest if digest and case.digest else None
        ),
        "trace_ids": list(evidence.trace_ids),
        "started_at": evidence.started_at,
        "ended_at": evidence.ended_at,
        "transcript": _transcript(evidence, stored.left_out if stored else 0),
        **_tools(evidence),
        "starting_state": _part("missing", STATE_MISSING),
        "labels": _labels(evidence),
        "expected_outcome": _expected(evidence),
    }
    if stored is not None:
        none = _part("missing", TRANSCRIPT_NO_TOOLS)
        shown |= {
            "tool_calls": {**none, "calls": []},
            "tool_inputs": none,
            "tool_outputs": none,
            "starting_state": _part("missing", TRANSCRIPT_STATE_MISSING),
            "labels": _part("missing", TRANSCRIPT_NO_LABELS, labels=[]),
        }
    elif row:
        none = _part("missing", ROW_NO_TOOLS)
        shown |= {
            "tool_calls": {**none, "calls": []},
            "tool_inputs": none,
            "tool_outputs": none,
            "starting_state": _part("missing", ROW_STATE_MISSING),
            "expected_outcome": _expected(evidence, ROW_NO_EXPECTED),
        }
        if not evidence.labels:
            shown["labels"] = _part("missing", ROW_NO_LABELS, labels=[])
    return shown


def _expected(evidence: SourceEvidence, unrecorded: str = NO_EXPECTED) -> dict:
    """The expected outcome part; ``unrecorded`` says why there is none."""
    text = evidence.expected
    if text is None:
        return _part("missing", unrecorded, text="")
    if not text:
        return _part("missing", ROW_EXPECTED_EMPTY, text="")
    if evidence.expected_truncated:
        return _part("truncated", CUT_TO_FIT, text=text)
    return _part("present", text=text)


def _new_checks(case: EvidenceImportCase) -> list[dict]:
    return [
        {"name": str(one.get("name") or ""), "what": str(one.get("what") or "")}
        for one in case.new_sub_goals or []
    ]


def _part(status: str, reason: str = "", **items) -> dict:
    return {"status": status, "reason": reason, **items}


def _transcript(evidence: SourceEvidence, left_out: int = 0) -> dict:
    """The transcript part; ``left_out`` system or tool messages are named."""
    part = _turns_part(evidence)
    if left_out:
        note = (
            f"{left_out} system or tool message{'s' if left_out != 1 else ''} "
            "left out: only what the user and the agent said is kept."
        )
        part["reason"] = f"{part['reason']} {note}".strip()
    return part


def _turns_part(evidence: SourceEvidence) -> dict:
    turns = [asdict(turn) for turn in evidence.turns]
    if evidence.transcript_unreadable and not turns:
        return _part(
            "unreadable", "The recorded transcript could not be read.", turns=[]
        )
    if not turns:
        return _part("missing", "No conversation text was recorded.", turns=[])
    if evidence.turns_omitted:
        count = evidence.turns_omitted
        return _part(
            "truncated",
            f"{count} middle turn{'s' if count != 1 else ''} left out to fit; "
            "the first and last are kept.",
            turns=turns,
        )
    if evidence.turns_truncated or any(t["truncated"] for t in turns):
        return _part("truncated", CUT_TO_FIT, turns=turns)
    return _part("present", turns=turns)


def _tools(evidence: SourceEvidence) -> dict:
    if evidence.tool_calls is None:
        unreadable = _part("unreadable", "The tool calls could not be read.")
        return {
            "tool_calls": {**unreadable, "calls": []},
            "tool_inputs": unreadable,
            "tool_outputs": unreadable,
        }
    calls = [asdict(call) for call in evidence.tool_calls]
    if not calls and evidence.tool_calls_truncated:
        cut = _part("truncated", "More steps were recorded than could be shown.")
        return {
            "tool_calls": {**cut, "calls": []},
            "tool_inputs": cut,
            "tool_outputs": cut,
        }
    if not calls:
        # Observe cannot tell an agent that called no tool from one whose
        # tools are not traced.
        missing = _part(
            "missing",
            "No tool calls were recorded, or the agent's tools are not traced.",
        )
        return {
            "tool_calls": {**missing, "calls": []},
            "tool_inputs": missing,
            "tool_outputs": missing,
        }
    listed = (
        _part("truncated", "More tool calls were recorded than are shown.", calls=calls)
        if evidence.tool_calls_truncated
        else _part("present", calls=calls)
    )
    return {
        "tool_calls": listed,
        "tool_inputs": _values(calls, "input", "inputs"),
        "tool_outputs": _values(calls, "output", "outputs"),
    }


def _values(calls: list[dict], field: str, plural: str) -> dict:
    recorded = [call for call in calls if call[field]]
    if not recorded:
        return _part("missing", f"The tool calls recorded no {plural}.")
    if len(recorded) < len(calls):
        return _part("partial", f"Some tool calls recorded no {plural}.")
    if any(call[f"{field}_truncated"] for call in calls):
        return _part("truncated", CUT_TO_FIT)
    return _part("present")


def _labels(evidence: SourceEvidence) -> dict:
    labels = [asdict(label) for label in evidence.labels]
    if not labels:
        return _part("missing", "Nobody labelled this source.", labels=[])
    if evidence.labels_truncated:
        return _part("truncated", CUT_TO_FIT, labels=labels)
    return _part("present", labels=labels)


def _observe_kind(evidence_import: EvidenceImport) -> SourceKind:
    # The model stores the kind as text; its choices are exactly Observe's kinds.
    return cast(SourceKind, evidence_import.source_kind)


def _import_kind(evidence_import: EvidenceImport) -> ImportKind:
    # The model stores the kind as text; its choices are exactly these.
    return cast(ImportKind, evidence_import.source_kind)


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
            values.append((case, "labels", i, "notes", label.notes))
            if isinstance(label.value, str):
                values.append((case, "labels", i, "value", label.value))
        if source.expected:
            values.append((case, "expected_outcome", 0, "text", source.expected))
    return values


def _analysed(
    texts: list[str],
) -> tuple[list[list[PiiFinding]], RedactionMethod, str]:
    """Each text's findings, analysed in one batch, with the method and its
    reason. Without Presidio, a fixed-shape match stands in."""
    found = find_pii(texts)
    if found is None:
        return find_pii_basic(texts), "basic", BASIC_REDACTION
    return found, "presidio", ""


def _redaction(
    cases: list[EvidenceImportCase], evidence: dict[str, SourceEvidence | None]
) -> dict:
    """What redaction would replace in every text the bundle carries."""
    values = _texts(cases, evidence)
    found, method, reason = _analysed([text for *_, text in values])
    findings = [
        {
            "case_id": case.id,
            "part": part,
            "index": index,
            "field": field,
            **asdict(finding),
        }
        for (case, part, index, field, _), hits in zip(values, found, strict=True)
        for finding in hits
    ]
    return {
        "method": method,
        "reason": reason,
        "findings": findings[:MAX_FINDINGS],
        "total": len(findings),
    }


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
        evidence = current_evidence(evidence_import, cases)
    values = _texts(cases, evidence)
    if is_transcript(evidence_import):
        # Redacted when it was stored.
        reason = StoredRedaction.model_validate(evidence_import.redaction).reason
        redacted = {
            (case.id, part, index, field): text
            for case, part, index, field, text in values
        }
    else:
        found, _method, reason = _analysed([text for *_, text in values])
        redacted = {
            (case.id, part, index, field): redact(text, hits)
            for (case, part, index, field, text), hits in zip(
                values, found, strict=True
            )
        }
    lines = _document_head(evidence_import, reason)
    for case in cases:
        source = evidence.get(case.source_id)
        if source is None:
            continue
        lines += [
            "",
            _document_case(evidence_import, case, source),
            f"-- transcript: {_transcript(source)['status']}",
        ]
        for i, turn in enumerate(source.turns):
            lines += _wrapped(
                f"[turn {i + 1}] {turn.role}",
                redacted[(case.id, "transcript", i, "text")],
            )
        lines.append(f"-- tool calls: {_tools(source)['tool_calls']['status']}")
        for i, call in enumerate(source.tool_calls or ()):
            lines.append(
                f"[call {i + 1}] {call.name} at {call.at.isoformat()}"
                + (" FAILED" if call.failed else "")
            )
            for field in ("input", "output", "error"):
                text = redacted[(case.id, "tool_calls", i, field)]
                if text:
                    lines += _wrapped(f"[call {i + 1}] {field}", text)
        lines.append(f"-- labels: {_labels(source)['status']}")
        for i, label in enumerate(source.labels):
            value = (
                redacted[(case.id, "labels", i, "value")]
                if isinstance(label.value, str)
                else json.dumps(label.value)
            )
            lines += _wrapped(
                f"[label {i + 1}] {label.name} on the {label.target}", value
            )
            notes = redacted[(case.id, "labels", i, "notes")]
            if notes:
                lines += _wrapped(f"[label {i + 1}] notes", notes)
        if is_dataset(evidence_import):
            lines.append(f"-- expected outcome: {_expected(source)['status']}")
            if source.expected:
                lines += _wrapped(
                    "[expected outcome]",
                    redacted[(case.id, "expected_outcome", 0, "text")],
                )
    return "\n".join(lines) + "\n"


def _document_head(evidence_import: EvidenceImport, reason: str) -> list[str]:
    """What the evidence document says before its cases: where the evidence
    comes from, whether it is production, and what redaction did."""
    redaction = "# Personal data is replaced with tokens such as <EMAIL_ADDRESS>." + (
        f" {reason}" if reason else ""
    )
    if is_transcript(evidence_import):
        return [
            f"# Evidence import {evidence_import.id}: transcripts a person added. "
            "They are not recordings Observe made: never cite a trace or a call.",
            (
                "# The person says they come from production: each case is "
                "regression evidence."
                if evidence_import.declared_production
                else "# The person did not say they come from production. Never "
                "write a case as something a customer did."
            ),
            redaction,
            "# A transcript records no starting state, tool calls or labels: "
            "anything you assume about the starting state is synthetic and must "
            "be labelled so.",
        ]
    if is_dataset(evidence_import):
        return [
            f"# Evidence import {evidence_import.id}: rows a person picked from "
            f"dataset {evidence_import.dataset_id}. They are not recordings "
            "Observe made: never cite a trace or a call.",
            (
                "# The person says they come from production: each case is "
                "regression evidence."
                if evidence_import.declared_production
                else "# The person did not say they come from production. Never "
                "write a case as something a customer did."
            ),
            redaction,
            "# A dataset row records no starting state or tool calls: anything you "
            "assume about the starting state is synthetic and must be labelled so.",
            "# Labels are the row's label columns: input evidence. The expected "
            "outcome is the row's expected outcome column.",
        ]
    kind = evidence_import.source_kind
    return [
        f"# Evidence import {evidence_import.id}: {kind}s "
        f"from Observe project {evidence_import.project_id}.",
        (
            f"# These {kind}s come from a Simulator project: simulated, not "
            "production. Never write a case as something a customer did."
            if is_simulated(evidence_import)
            else f"# These are production {kind}s: each case is regression evidence."
        ),
        redaction,
        "# Observe never records the starting state: anything you assume about "
        "it is synthetic and must be labelled so.",
        "# Labels are what annotators said: input evidence, not the expected answer.",
    ]


def _document_case(
    evidence_import: EvidenceImport, case: EvidenceImportCase, source: SourceEvidence
) -> str:
    if source.started_at is None or source.ended_at is None:
        noun = source_noun(evidence_import.source_kind)
        return f"== case {case.id} ({noun} {case.source_id})"
    return (
        f"== case {case.id} ({evidence_import.source_kind} {case.source_id}, "
        f"traces {', '.join(source.trace_ids)}, "
        f"{source.started_at.isoformat()} to {source.ended_at.isoformat()})"
    )


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
