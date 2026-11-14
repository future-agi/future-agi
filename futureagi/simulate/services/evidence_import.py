"""Making an evidence import: the Observe sessions or calls a person picked,
frozen for one environment so a test can be drafted from them.

Nothing here drafts a scenario or starts a run.
"""

from __future__ import annotations

import json

from django.db import transaction
from django.utils import timezone

from simulate.models import EvidenceImport, EvidenceImportCase, HostedHarnessJob
from simulate.selectors.evidence_import import (
    SOURCE_NOT_FOUND,
    evidence_digest,
    import_evidence,
)
from simulate.services.evidence_drafts import CATALOGUE
from simulate.services.harness_versioning import archives, drafts
from simulate.services.harness_versioning.versions import suite_documents
from simulate.services.hosted_harness import HostedHarnessError
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


def _case(
    evidence_import: EvidenceImport,
    position: int,
    source_id: str,
    evidence: SourceEvidence | None,
) -> EvidenceImportCase:
    if evidence is None:
        return EvidenceImportCase(
            evidence_import=evidence_import,
            position=position,
            source_id=source_id,
            status=EvidenceImportCase.Status.UNAVAILABLE,
            reason=SOURCE_NOT_FOUND,
        )
    return EvidenceImportCase(
        evidence_import=evidence_import,
        position=position,
        source_id=source_id,
        digest=evidence_digest(evidence),
    )


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
        if environment_version is None or any(
            case.environment_hash != environment_version.hash for case in cases
        ):
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
