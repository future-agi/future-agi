"""Making an evidence import: the Observe sessions or calls or the dataset rows
a person picked, or transcripts they added, frozen for one environment so a
test can be drafted from them.

Nothing here drafts a scenario or starts a run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from model_hub.models.develop_dataset import Dataset
from simulate.models import EvidenceImport, EvidenceImportCase, HostedHarnessJob
from simulate.pydantic_schemas.evidence_import import DatasetColumns
from simulate.selectors.dataset_evidence import MEDIA_TYPES, dataset_columns
from simulate.selectors.evidence_import import (
    OPEN_CASE_STATUSES,
    ROW_NOT_FOUND,
    SOURCE_NOT_FOUND,
    evidence_digest,
    import_evidence,
    import_rows,
    stored_transcripts,
)
from simulate.services.evidence_drafts import CATALOGUE
from simulate.services.harness_versioning import archives, drafts
from simulate.services.harness_versioning.versions import suite_documents
from simulate.services.hosted_harness import HostedHarnessError
from simulate.utils.transcript_parse import (
    Format,
    ParsedTranscript,
    TranscriptError,
    parse_transcript,
)
from tracer.models.project import Project
from tracer.selectors.observe_evidence import SourceEvidence, SourceKind


def create_evidence_import(
    *,
    environment: HostedHarnessJob,
    project: Project,
    source_kind: SourceKind,
    source_ids: list[str],
    user,
) -> tuple[EvidenceImport, dict[str, SourceEvidence]]:
    """Freeze ``source_ids`` as an import for ``environment``, each with a
    fingerprint of what it holds now (blank when part of it could not be read).
    Returns the import and the evidence it was made from, so the caller can
    show it without reading Observe again."""
    evidence = import_evidence(project, source_kind, source_ids)
    with transaction.atomic():
        evidence_import = EvidenceImport.objects.create(
            organization_id=environment.organization_id,
            workspace_id=environment.workspace_id,
            environment=environment,
            project=project,
            agent_version_id=environment.agent_version_id,
            source_kind=source_kind,
            created_by=user,
        )
        EvidenceImportCase.objects.bulk_create(
            [
                _case(evidence_import, position, source_id, evidence.get(source_id))
                for position, source_id in enumerate(source_ids)
            ]
        )
    return evidence_import, evidence


def create_dataset_import(
    *,
    environment: HostedHarnessJob,
    dataset: Dataset,
    columns: DatasetColumns,
    row_ids: list[str],
    production: bool,
    user,
) -> tuple[EvidenceImport, dict[str, SourceEvidence]]:
    """Freeze ``row_ids`` of ``dataset`` as an import for ``environment``, read
    through ``columns``, each row with a fingerprint of what it holds now, as an
    Observe import's sources are. Nothing of the rows is stored: they are read
    again on every view. ``production`` is what the person said about where
    they come from. A column that is not the dataset's, or holds media, refuses
    the import."""
    _check_columns(dataset, columns)
    evidence = import_rows(dataset, columns, row_ids)
    with transaction.atomic():
        evidence_import = EvidenceImport.objects.create(
            organization_id=environment.organization_id,
            workspace_id=environment.workspace_id,
            environment=environment,
            dataset=dataset,
            dataset_columns=columns.model_dump(mode="json"),
            agent_version_id=environment.agent_version_id,
            source_kind=EvidenceImport.SourceKind.DATASET,
            declared_production=production,
            created_by=user,
        )
        EvidenceImportCase.objects.bulk_create(
            [
                _case(
                    evidence_import,
                    position,
                    row_id,
                    evidence.get(row_id),
                    missing=ROW_NOT_FOUND,
                )
                for position, row_id in enumerate(row_ids)
            ]
        )
    return evidence_import, evidence


def _check_columns(dataset: Dataset, columns: DatasetColumns) -> None:
    """Refuses a picked column the dataset does not have, and one whose cells
    are audio, images or documents: an import reads text only."""
    picked = {
        "conversation": [columns.conversation],
        "labels": columns.labels,
        "expected": [columns.expected] if columns.expected is not None else [],
    }
    live = dataset_columns(dataset, [one for ids in picked.values() for one in ids])
    for role, ids in picked.items():
        for column_id in map(str, ids):
            column = live.get(column_id)
            if column is None:
                raise HostedHarnessError(
                    "dataset_column_not_found",
                    "A picked column is no longer in this dataset; pick the "
                    "columns again",
                    details={"column": role},
                )
            if column.data_type in MEDIA_TYPES:
                raise HostedHarnessError(
                    "dataset_column_media",
                    f'Column "{column.name}" holds {column.data_type}, not text: '
                    "rows whose conversation is only audio cannot be imported yet",
                    details={"column": role},
                )


@dataclass(frozen=True)
class TranscriptInput:
    """One transcript as a person pasted or uploaded it."""

    name: str
    form: Format
    text: str


def create_transcript_import(
    *,
    environment: HostedHarnessJob,
    transcripts: list[TranscriptInput],
    production: bool,
    user,
) -> EvidenceImport:
    """Store ``transcripts`` as an import for ``environment``, one case each.
    Only their redacted text is stored, cut to the import's size, and it lives
    until the import is deleted. ``production`` is what the person said about
    where they come from. A transcript that cannot be read refuses the whole
    import, naming it and its line."""
    parsed = [_parsed(number, one) for number, one in enumerate(transcripts, 1)]
    stored, redaction = stored_transcripts(
        [(one.name, read) for one, read in zip(transcripts, parsed, strict=True)]
    )
    with transaction.atomic():
        evidence_import = EvidenceImport.objects.create(
            organization_id=environment.organization_id,
            workspace_id=environment.workspace_id,
            environment=environment,
            agent_version_id=environment.agent_version_id,
            source_kind=EvidenceImport.SourceKind.TRANSCRIPT,
            declared_production=production,
            redaction=redaction.model_dump(mode="json"),
            created_by=user,
        )
        EvidenceImportCase.objects.bulk_create(
            [
                EvidenceImportCase(
                    evidence_import=evidence_import,
                    position=position,
                    source_id=f"transcript-{position + 1}",
                    transcript=one.model_dump(mode="json"),
                )
                for position, one in enumerate(stored)
            ]
        )
    return evidence_import


def _parsed(number: int, transcript: TranscriptInput) -> ParsedTranscript:
    try:
        return parse_transcript(transcript.text, transcript.form)
    except TranscriptError as error:
        raise HostedHarnessError(
            "transcript_unreadable",
            f"Transcript {number} ({transcript.name}): {error}",
            details={"transcript": number, "line": error.line},
        ) from None


def _case(
    evidence_import: EvidenceImport,
    position: int,
    source_id: str,
    evidence: SourceEvidence | None,
    *,
    missing: str = SOURCE_NOT_FOUND,
) -> EvidenceImportCase:
    if evidence is None:
        return EvidenceImportCase(
            evidence_import=evidence_import,
            position=position,
            source_id=source_id,
            status=EvidenceImportCase.Status.UNAVAILABLE,
            reason=missing,
        )
    return EvidenceImportCase(
        evidence_import=evidence_import,
        position=position,
        source_id=source_id,
        digest=evidence_digest(evidence),
    )


def discard_evidence_import(evidence_import: EvidenceImport) -> bool:
    """Discard what a person no longer wants of ``evidence_import``. Nothing of
    it added: the import is deleted, and a transcript's stored text with it.
    Some of it added: only its cases not added are discarded, so the added
    scenarios keep their provenance, and the import counts as finished. Refused
    while the builder drafts its cases. Returns whether the import was deleted."""
    with transaction.atomic():
        locked = EvidenceImport.no_workspace_objects.select_for_update().get(
            id=evidence_import.id
        )
        if locked.drafting_job_id is not None:
            raise HostedHarnessError(
                "evidence_drafting_busy",
                "This import's cases are being drafted; discard it once drafting ends",
                status_code=409,
            )
        cases = EvidenceImportCase.objects.filter(evidence_import=locked)
        if not cases.filter(status=EvidenceImportCase.Status.ADDED).exists():
            locked.delete()
            return True
        cases.filter(status__in=OPEN_CASE_STATUSES).update(
            status=EvidenceImportCase.Status.DISCARDED, updated_at=timezone.now()
        )
        return False


def add_to_suite(
    evidence_import: EvidenceImport,
    case_ids: list[str],
    *,
    expected: dict[str, drafts.DraftRef],
    idempotency_key: str,
    user,
) -> bool:
    """Add the drafted scenarios of ``case_ids`` to the environment's suite, in one
    transaction under the environment's lock. ``expected`` is the unsaved changes
    the person saw; any edit since refuses. When they saw none, the suite is saved
    as well, so no open draft is left to block Fix my agent; beside other edits it
    joins the draft only, since nobody else's changes are made live for them. A
    refused save leaves everything as it was. Returns whether it was saved."""
    wanted = {str(case_id) for case_id in case_ids}
    with transaction.atomic():
        locked = (
            HostedHarnessJob.no_workspace_objects.select_for_update(of=("self",))
            .select_related("environment_version", "suite_version")
            .get(id=evidence_import.environment_id)
        )
        seen = {
            piece: expected.get(piece, drafts.NO_DRAFT) for piece in drafts.DRAFT_PIECES
        }
        if drafts.draft_refs(locked) != seen:
            raise HostedHarnessError(
                "draft_changed",
                "The unsaved changes moved since the review opened; look at them "
                "and add again",
                status_code=409,
            )
        cases = list(
            EvidenceImportCase.objects.select_for_update()
            .filter(
                evidence_import=evidence_import,
                id__in=wanted,
                status=EvidenceImportCase.Status.DRAFTED,
            )
            .order_by("position")
        )
        if not wanted or len(cases) != len(wanted):
            raise HostedHarnessError(
                "evidence_case_not_drafted",
                "Only this import's drafted cases can be added",
                status_code=409,
            )
        environment_version = (
            drafts.fresh_draft(locked, "environment") or locked.environment_version
        )
        if any(case.environment_hash != environment_version.hash for case in cases):
            raise HostedHarnessError(
                "evidence_draft_stale",
                "The environment changed since these cases were drafted; draft "
                "them again",
                status_code=409,
            )
        pieces = {"suite", "environment"} if _checks(cases) else {"suite"}
        write = drafts.record_draft(
            locked,
            _with_cases(locked, environment_version, cases),
            origin="manual",
            user=user,
            expected=seen,
            pieces=pieces,
        )
        if write is None or write.suite is None:
            raise HostedHarnessError(
                "drafts_unavailable",
                "This environment keeps no unsaved changes, so imported cases "
                "cannot be added",
                status_code=409,
            )
        EvidenceImportCase.objects.filter(id__in=[case.id for case in cases]).update(
            status=EvidenceImportCase.Status.ADDED, updated_at=timezone.now()
        )
        saved = all(ref == drafts.NO_DRAFT for ref in seen.values())
        if saved:
            drafts.save_drafts(
                locked,
                {
                    piece: drafts.SaveRequest(
                        expected_parent_id=draft.parent_id,
                        expected_revision=draft.revision,
                    )
                    for piece, draft in (
                        ("environment", write.environment),
                        ("suite", write.suite),
                    )
                    if draft is not None
                },
                idempotency_key=idempotency_key,
                user=user,
            )
        transaction.on_commit(lambda: drafts.retire_chat_runtime(locked))
    return saved


def _with_cases(
    environment: HostedHarnessJob,
    environment_version,
    cases: list[EvidenceImportCase],
) -> bytes:
    """The environment's authoring tree with each case's scenario folder added to
    its suite (the open draft's, else the default's)."""
    suite = dict(
        drafts._piece_files(
            environment,
            "suite",
            drafts.fresh_draft(environment, "suite") or environment.suite_version,
        )
    )
    documents = suite_documents(suite)
    for case in cases:
        files = archives.read_archive(archives.get_archive(case.proposal_object_key))
        suite.update(files)
        document = json.loads(files[f"scenarios/{case.scenario_key}/scenario.json"])
        documents.append(
            {
                **document,
                "scenario_key": case.scenario_key,
                "folder": f"scenarios/{case.scenario_key}",
            }
        )
    suite["scenarios.json"] = json.dumps(documents, indent=2).encode()
    environment_files = dict(
        drafts._piece_files(environment, "environment", environment_version)
    )
    if added := _checks(cases):
        catalogue = json.loads(environment_files[CATALOGUE])
        known = {one.get("name") for one in catalogue.get("sub_goals") or []}
        catalogue["sub_goals"] = [
            *(catalogue.get("sub_goals") or []),
            *(one for one in added if one.get("name") not in known),
        ]
        environment_files[CATALOGUE] = json.dumps(catalogue, indent=2).encode()
    return archives.pack_archive(archives.compose(environment_files, suite))


def _checks(cases: list[EvidenceImportCase]) -> list[dict]:
    """The checks the cases add, each name once."""
    found: dict[str, dict] = {}
    for case in cases:
        for one in case.new_sub_goals or []:
            found.setdefault(one.get("name"), one)
    return list(found.values())
