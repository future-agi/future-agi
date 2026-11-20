"""Validate: candidate versions built from the open drafts and the decisions on them, and their validation started.

Each drafted piece becomes its default plus exactly the accepted changes (its draft's own files when nothing of it
was rejected). The drafts are not written, so a failed validation leaves them as the person left them. Save promotes
the candidates (`candidate_promotion`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypedDict

import structlog
from django.db import transaction
from django.utils import timezone

from simulate.models import AgentVersion, HarnessSuiteVersion, HostedHarnessJob
from simulate.models.harness_versioning import HashKind, VersionStatus
from simulate.services.harness_versioning import archives, review, validation
from simulate.services.harness_versioning.candidate_sets import (
    CandidateDraft,
    CandidateSet,
    CandidateValidation,
    current_defaults,
    live_set,
    store_set,
    supersede_candidates,
    validation_available,
    validation_state,
    version_id_of,
)
from simulate.services.harness_versioning.draft_pieces import (
    DRAFT_PIECES,
    active_twin,
    draft_content,
    piece_files,
    piece_model,
    prepare_tree,
)
from simulate.services.harness_versioning.drafts import (
    open_drafts,
    require_revisions,
    require_saveable,
)
from simulate.services.harness_versioning.review import DraftReview
from simulate.services.harness_versioning.versions import (
    lock_environment,
)

logger = structlog.get_logger(__name__)


class CandidateIds(TypedDict):
    environment_version_id: str
    suite_version_id: str


class Unavailable(TypedDict):
    state: Literal["unavailable"]


class ValidateBody(TypedDict):
    """Validate's answer: where the validation stands, and the candidate versions it is proving."""

    validation: CandidateValidation | Unavailable
    candidates: CandidateIds | None


UNAVAILABLE = ValidateBody(validation=Unavailable(state="unavailable"), candidates=None)


def _candidate(
    locked: HostedHarnessJob,
    piece: str,
    draft: Any,
    prepared: Any,
    *,
    documents: Mapping[str, Any],
    user: Any,
) -> Any:
    """The version a piece's composed content becomes: an active version with the same content, a live candidate
    built from the same defaults (which now carries this Validate's decisions and draft revisions), or a new one.
    """
    model = piece_model(piece)
    content_hash = (
        prepared.environment_hash if piece == "environment" else prepared.suite_hash
    )
    twin = active_twin(model, locked, content_hash, HashKind.TREE_V1)
    if twin is not None:
        return twin
    live = model.no_workspace_objects.filter(
        job=locked,
        status=VersionStatus.CANDIDATE,
        hash=content_hash,
        documents__base_defaults=documents["base_defaults"],
    )
    if model is HarnessSuiteVersion:
        live = live.filter(fix_run__isnull=True)
    live = live.first()
    if live is not None:
        live.documents = {**(live.documents or {}), **documents}
        live.save(update_fields=["documents", "updated_at"])
        return live
    archives.put_archive(
        *(
            (prepared.environment_key, prepared.environment_body)
            if piece == "environment"
            else (prepared.suite_key, prepared.suite_body)
        )
    )
    current = getattr(locked, f"{piece}_version")
    values = draft_content(
        locked, piece, prepared, origin=draft.origin, default=current
    )
    values["documents"].update(documents)
    return model.no_workspace_objects.create(
        job=locked,
        organization_id=locked.organization_id,
        workspace_id=locked.workspace_id,
        status=VersionStatus.CANDIDATE,
        parent=current,
        origin=draft.origin,
        created_by=user,
        last_edited_by=user,
        nudge=draft.nudge,
        **values,
    )


def create_candidates(
    locked: HostedHarnessJob,
    opened: Mapping[str, Any],
    reviewed: DraftReview,
    *,
    user: Any,
) -> dict[str, Any]:
    """Both pieces of the pairing to validate: each drafted piece's default plus exactly its accepted changes, the
    other piece's default. The drafts are not written."""
    base_defaults = current_defaults(locked)
    files = {
        piece: (
            review.composed_files(locked, piece, opened[piece], reviewed)
            if piece in opened
            else piece_files(locked, piece, getattr(locked, f"{piece}_version"))
        )
        for piece in DRAFT_PIECES
    }
    _, prepared = prepare_tree(locked, {**files["environment"], **files["suite"]})
    revisions = {piece: draft.revision for piece, draft in opened.items()}
    versions = {}
    for piece in DRAFT_PIECES:
        draft = opened.get(piece)
        if draft is None:
            versions[piece] = getattr(locked, f"{piece}_version")
            continue
        documents = {
            "base_defaults": base_defaults,
            "draft_revisions": revisions,
            "review": (draft.documents or {}).get("review"),
            "review_changes": review.review_changes(reviewed, piece),
            "provenance": (draft.documents or {}).get("provenance"),
        }
        versions[piece] = _candidate(
            locked, piece, draft, prepared, documents=documents, user=user
        )
    return versions


def _same_versions(entry: CandidateSet | None, versions: Mapping[str, Any]) -> bool:
    return entry is not None and all(
        version_id_of(entry, piece) == str(versions[piece].id) for piece in DRAFT_PIECES
    )


def validate_drafts(
    environment: HostedHarnessJob,
    *,
    expected_revisions: Mapping[str, int],
    expected_review_digest: str | None,
    user: Any,
) -> tuple[ValidateBody, bool]:
    """Build candidates from the open drafts and the decisions on them, and start validating them.

    Returns the body and whether a validation job is running (202) rather than already settled (200). Refuses as
    today's Save does (`chat_working`, `environment_busy`, `rebuild_running`, `draft_stale`, retired or taken keys),
    then a review that moved (`review_stale`), an undecided change once any is decided (`review_undecided`), an
    accepted change that needs a rejected one (`change_depends_on_rejected`) and a draft that moved since the
    person read it (`draft_changed`). The live set always records this Validate's decisions and draft revisions.
    """
    with transaction.atomic():
        locked = lock_environment(environment)
        opened = open_drafts(locked)
        require_saveable(locked, opened, expected_revisions={})
        review.heal_suite_index(locked)
        # Read again: a healed suite draft has a new archive.
        opened = open_drafts(locked)
        reviewed = review.draft_review(locked)
        review.check_review(
            reviewed, expected_review_digest, digest_required=True, environment=locked
        )
        require_revisions(opened, expected_revisions, required=True)
        # A pairing that can be validated pins an agent version.
        if not validation_available(locked) or locked.agent_version_id is None:
            return UNAVAILABLE, False
        versions = create_candidates(locked, opened, reviewed, user=user)
        started = validation.start_validation(
            locked,
            AgentVersion.no_workspace_objects.get(id=locked.agent_version_id),
            versions["environment"],
            versions["suite"],
            purpose="save",
        )
        if started is None:
            # The agent's side row went away after the check above: nothing can certify this pairing.
            supersede_candidates(locked)
            if live_set(locked) is not None:
                store_set(locked, None)
            return UNAVAILABLE, False
        if not _same_versions(live_set(locked), versions):
            supersede_candidates(
                locked, keep={str(one.id) for one in versions.values()}
            )
        entry: CandidateSet = {
            "environment_version_id": str(versions["environment"].id),
            "suite_version_id": str(versions["suite"].id),
            "agent_version_id": str(locked.agent_version_id),
            "assembly_key": started.bundle.assembly_key,
            "base_defaults": current_defaults(locked),
            "drafts": {
                piece: CandidateDraft(id=str(draft.id), revision=draft.revision)
                for piece, draft in opened.items()
            },
            "review_digest": reviewed["review_digest"],
            "job_id": str(started.job.id) if started.job is not None else None,
            "at": timezone.now().isoformat(),
        }
        store_set(locked, entry)
        state = validation_state(locked, entry)
    assert state is not None  # a stored candidate set always has a state
    logger.info(
        "harness_drafts_validated",
        environment_id=str(locked.id),
        environment_version_id=entry["environment_version_id"],
        suite_version_id=entry["suite_version_id"],
        state=state["state"],
        job_id=entry["job_id"],
    )
    body = ValidateBody(
        validation=state,
        candidates=CandidateIds(
            environment_version_id=entry["environment_version_id"],
            suite_version_id=entry["suite_version_id"],
        ),
    )
    return body, state["state"] == "validating"
