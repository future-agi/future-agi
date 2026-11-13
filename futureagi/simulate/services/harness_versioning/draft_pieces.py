"""The pieces of an environment's drafts that Save, Validate and review share: which model holds a piece, a piece's
files, a tree prepared as draft content, and the refusals every write and save makes.

Nothing here writes a default or reads a review; ``drafts``, ``review`` and ``candidates`` build on it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from minio.error import S3Error

from simulate.models import (
    HarnessEnvironmentVersion,
    HarnessSuiteVersion,
    HostedHarnessJob,
)
from simulate.models.harness_versioning import DiscardReason, HashKind, VersionStatus
from simulate.services.harness_versioning import archives
from simulate.services.harness_versioning.versions import (
    SCENARIO_COUNT_MAX,
    USER_ORIGINS,
    _refuse_retired_keys,
    _refuse_taken_keys,
    _stage_outputs,
    environment_change,
    prepare_pieces,
    suite_change,
)
from simulate.services.hosted_harness import HostedHarnessError

DRAFT_PIECES = ("environment", "suite")


def piece_model(piece: str) -> Any:
    return HarnessEnvironmentVersion if piece == "environment" else HarnessSuiteVersion


def piece_of(version: Any) -> str:
    return "environment" if isinstance(version, HarnessEnvironmentVersion) else "suite"


def version_error(
    code: str, message: str, status_code: int, **details: Any
) -> HostedHarnessError:
    return HostedHarnessError(code, message, status_code=status_code, details=details)


def open_draft(
    environment: HostedHarnessJob, piece: str, *, lock: bool = False
) -> Any | None:
    """The piece's open draft, fresh or stale."""
    drafts = piece_model(piece).no_workspace_objects.filter(
        job_id=environment.id, status=VersionStatus.DRAFT
    )
    if lock:
        drafts = drafts.select_for_update()
    return drafts.first()


def is_stale(draft: Any, environment: HostedHarnessJob) -> bool:
    """A draft started from a default that has moved since."""
    return bool(
        draft.parent_id != getattr(environment, f"{piece_of(draft)}_version_id")
    )


def piece_side(files: Mapping[str, bytes], piece: str) -> dict[str, bytes]:
    split = archives.split_authoring(files)
    return split.environment if piece == "environment" else split.suite


def piece_files(
    environment: HostedHarnessJob, piece: str, version: Any
) -> dict[str, bytes]:
    """A version's files; an environment with no version yet, or a backfilled version whose
    archive is gone, reads the default pair's side instead."""
    fallback = str(
        ((environment.payload or {}).get("metadata") or {}).get("authoring_object_key")
        or ""
    )
    if version is None:
        return (
            piece_side(archives.read_archive(archives.get_archive(fallback)), piece)
            if fallback
            else {}
        )
    try:
        return piece_side(
            archives.read_archive(archives.get_archive(version.archive_object_key)),
            piece,
        )
    except (S3Error, HostedHarnessError):
        if not fallback:
            raise
        return piece_side(archives.read_archive(archives.get_archive(fallback)), piece)


def document_token(document: Mapping[str, Any]) -> str:
    from simulate.services.hosted_harness_gateway import _scenario_token

    return _scenario_token(document.get("scenario_key") or document.get("name"))


def exempted_keys(draft: Any) -> set[str]:
    if draft is None:
        return set()
    return {str(one) for one in (draft.documents or {}).get("exempt_keys") or []}


def refuse_scenario_keys(
    locked: HostedHarnessJob,
    scenarios: list[dict[str, Any]],
    *,
    origin: str,
    exempt: set[str],
) -> None:
    """A person's edit may not reuse a dropped key or take another scenario's; a
    nudge's may not take one. Keys Extend placed in this draft are not refused again."""
    checked = [one for one in scenarios if document_token(one) not in exempt]
    if origin in USER_ORIGINS:
        _refuse_retired_keys(locked, checked)
    if origin in USER_ORIGINS or origin == "nudge":
        _refuse_taken_keys(locked, checked)


def prepare_tree(environment: HostedHarnessJob, files: Mapping[str, bytes]):
    split = archives.split_authoring(files)
    if "contract.json" not in split.environment or not any(
        path.startswith(archives.SUITE_ROOTS) for path in split.suite
    ):
        raise HostedHarnessError(
            "authoring_incomplete",
            "the authoring archive has no contract or no scenarios to version",
            status_code=422,
        )
    pair_body = archives.pack_archive(archives.compose(split.environment, split.suite))
    prepared = prepare_pieces(
        environment, split, outputs=_stage_outputs(pair_body), authoring_revision=""
    )
    # A draft stands for no authoring revision until it is saved.
    prepared.environment_documents["authoring_revisions"] = []
    prepared.suite_documents["authoring_revisions"] = []
    return split, prepared


def draft_content(
    locked: HostedHarnessJob,
    piece: str,
    prepared: Any,
    *,
    origin: str,
    default: Any,
) -> dict[str, Any]:
    """A draft row's content fields: archive, hash, documents and diff against the default."""
    if piece == "environment":
        diff, summary = environment_change(
            default, prepared.contract, prepared.environment_documents
        )
        return {
            **prepared.environment_fields,
            "documents": dict(prepared.environment_documents),
            "hash": prepared.environment_hash,
            "hash_kind": HashKind.TREE_V1,
            "summary": summary,
            "diff": diff,
            "agent_version_id": locked.agent_version_id,
        }
    diff, summary = suite_change(default, prepared.scenarios, origin)
    return {
        **prepared.suite_fields,
        "documents": dict(prepared.suite_documents),
        "hash": prepared.suite_hash,
        "hash_kind": HashKind.TREE_V1,
        "summary": summary,
        "diff": diff,
        "scenario_count": min(len(prepared.scenarios), SCENARIO_COUNT_MAX),
    }


def discard_version(draft: Any, *, user: Any = None, **documents: Any) -> None:
    draft.status = VersionStatus.DISCARDED
    draft.discard_reason = DiscardReason.USER
    fields = ["status", "discard_reason", "updated_at"]
    if user is not None:
        draft.last_edited_by = user
        fields.append("last_edited_by")
    if documents:
        draft.documents = {**(draft.documents or {}), **documents}
        fields.append("documents")
    draft.save(update_fields=fields)


def active_twin(model: Any, locked: HostedHarnessJob, draft: Any) -> Any | None:
    return (
        model.no_workspace_objects.filter(
            job=locked,
            hash=draft.hash,
            hash_kind=draft.hash_kind,
            status=VersionStatus.ACTIVE,
        )
        .order_by("-version_number")
        .first()
    )


def draft_changed(**details: Any) -> HostedHarnessError:
    return version_error(
        "draft_changed",
        "the unsaved changes were edited elsewhere while this chat was working; ask "
        "again to make the change on top of them",
        409,
        **details,
    )
