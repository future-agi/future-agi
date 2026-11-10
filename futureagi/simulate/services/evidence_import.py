"""Making an evidence import: the Observe sessions or calls a person picked,
frozen for one environment so a test can be drafted from them.

Nothing here drafts a scenario or starts a run.
"""

from __future__ import annotations

from django.db import transaction

from simulate.models import EvidenceImport, EvidenceImportCase, HostedHarnessJob
from simulate.selectors.evidence_import import (
    SOURCE_NOT_FOUND,
    evidence_digest,
    import_evidence,
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
