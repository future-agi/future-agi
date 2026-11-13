"""Save with candidates: a validated candidate set becomes the defaults in one step; and the daily sweep of
candidates nobody saved.

Promotion runs only while the pairing is certified, the defaults and the drafts are the ones the candidates were built
from, and the review is the one validated.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

import structlog
from django.db import transaction
from django.utils import timezone

from simulate.models import HarnessAssembledBundle, HostedHarnessJob
from simulate.models.harness_versioning import DiscardReason, VersionStatus
from simulate.services.harness_versioning import review
from simulate.services.harness_versioning.candidate_sets import (
    VALIDATION_KEY,
    CandidateSet,
    current_defaults,
    live_set,
    set_bundle,
    set_versions,
    store_set,
    supersede_candidates,
    unavailable_on_our_side,
    version_id_of,
)
from simulate.services.harness_versioning.candidates import edits_moved
from simulate.services.harness_versioning.draft_pieces import (
    DRAFT_PIECES,
    active_twin,
    discard_version,
    open_draft,
    piece_model,
    version_error,
)
from simulate.services.harness_versioning.drafts import (
    remember_save,
    replayed_save,
    require_chat_idle,
    require_no_rebuild,
    retire_chat_runtime,
    saved_body,
)
from simulate.services.harness_versioning.review_saved import name_saved
from simulate.services.harness_versioning.versions import (
    add_members,
    lock_environment,
    member_rows,
    next_number,
    note_revision,
    pair_archive,
    require_not_busy,
    set_defaults,
)
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

# How long a candidate set nobody saved is kept before the daily sweep discards it.
CANDIDATE_TTL = timedelta(days=7)


def _not_validated() -> HostedHarnessError:
    return version_error(
        "not_validated",
        "These changes haven't passed validation; validate them, then save",
        409,
    )


def _promote(
    locked: HostedHarnessJob, piece: str, version: Any, *, environment_version: Any
) -> tuple[Any, bool]:
    """A candidate becomes an active version numbered next; an active version with its content, if one appeared
    meanwhile, is used instead. An active version (the piece nothing changed) stays as it is; anything else, such as a
    candidate set aside meanwhile, is refused."""
    if version.status == VersionStatus.ACTIVE:
        return version, False
    if version.status != VersionStatus.CANDIDATE:
        raise _not_validated()
    model = piece_model(piece)
    twin = active_twin(model, locked, version)
    if twin is not None:
        version.status, version.discard_reason = (
            VersionStatus.DISCARDED,
            DiscardReason.SUPERSEDED,
        )
        version.save(update_fields=["status", "discard_reason", "updated_at"])
        return twin, False
    version.status = VersionStatus.ACTIVE
    version.version_number = next_number(model, locked)
    if piece == "suite":
        version.authored_against = environment_version
    version.save()
    return version, True


def promote_candidates(
    environment: HostedHarnessJob,
    candidates: Mapping[str, Any],
    *,
    expected_review_digest: str | None,
    name: str,
    idempotency_key: str,
    user: Any,
) -> dict[str, Any]:
    """Make a validated candidate set the defaults in one step, and set the drafts it came from aside.

    Refuses a set that is not the live one or is not certified (`not_validated`), one whose defaults moved
    (`defaults_moved`; the set reads superseded), drafts that moved (`draft_changed`) and a review that moved
    (`review_stale`). The same key repeats the same Save.
    """
    if not name.strip():
        raise version_error("name_required", "Name the new version, then save", 400)
    request = {
        "operation": "candidates",
        **{
            f"{piece}_version_id": str(candidates.get(f"{piece}_version_id") or "")
            for piece in DRAFT_PIECES
        },
        "name": name.strip(),
    }
    moved_defaults = False
    with transaction.atomic():
        locked = lock_environment(environment)
        replayed = replayed_save(locked, idempotency_key, request=request)
        if replayed is not None:
            return replayed
        require_not_busy(locked, "environment")
        require_no_rebuild(locked, piece="environment")
        require_chat_idle(locked)
        entry = live_set(locked)
        if entry is None or any(
            str(candidates.get(f"{piece}_version_id") or "")
            != version_id_of(entry, piece)
            for piece in DRAFT_PIECES
        ):
            raise _not_validated()
        if current_defaults(locked) != entry["base_defaults"]:
            supersede_candidates(locked)
            store_set(locked, None)
            moved_defaults = True
        else:
            activated = _promote_set(
                locked, entry, expected_review_digest=expected_review_digest, user=user
            )
            name_saved(activated, name=name)
            remember_save(locked, idempotency_key, activated, request)
            transaction.on_commit(lambda: retire_chat_runtime(locked))
    if moved_defaults:
        raise version_error(
            "defaults_moved",
            "The environment's versions changed since these changes were validated; validate again",
            409,
        )
    for piece, (version, created) in activated.items():
        logger.info(
            "harness_candidate_promoted",
            environment_id=str(locked.id),
            piece=piece,
            version_id=str(version.id),
            created=created,
        )
    return saved_body(
        locked, {piece: version for piece, (version, _) in activated.items()}
    )


def _promote_set(
    locked: HostedHarnessJob,
    entry: CandidateSet,
    *,
    expected_review_digest: str | None,
    user: Any,
) -> dict[str, tuple[Any, bool]]:
    bundle = set_bundle(locked, entry)
    certified = (
        bundle is not None and bundle.state == HarnessAssembledBundle.State.READY
    )
    if not certified and not unavailable_on_our_side(entry, bundle):
        raise _not_validated()
    opened = {piece: open_draft(locked, piece, lock=True) for piece in entry["drafts"]}
    for piece, seen in entry["drafts"].items():
        draft = opened[piece]
        if (
            draft is None
            or str(draft.id) != seen["id"]
            or draft.revision != seen["revision"]
        ):
            raise edits_moved(piece)
    if expected_review_digest and expected_review_digest != entry["review_digest"]:
        raise review.review_stale()
    if review.draft_review(locked)["review_digest"] != entry["review_digest"]:
        raise review.review_stale()
    built = set_versions(locked, entry)
    environment_version, created_environment = _promote(
        locked, "environment", built["environment"], environment_version=None
    )
    suite_version, created_suite = _promote(
        locked, "suite", built["suite"], environment_version=environment_version
    )
    if created_suite:
        # Paired while the old suite is still the default, as a recorded save pairs.
        add_members(
            suite_version,
            member_rows(locked, (suite_version.documents or {}).get("scenarios") or []),
        )
    targets = {"environment": environment_version, "suite": suite_version}
    moved = {
        piece
        for piece in DRAFT_PIECES
        if targets[piece].id != getattr(locked, f"{piece}_version_id")
    }
    if moved:
        payload = dict(locked.payload or {})
        metadata = dict(payload.get("metadata") or {})
        metadata.pop(VALIDATION_KEY, None)
        pair = pair_archive(
            locked, metadata, environment_version, suite_version, sorted(moved)[0]
        )
        set_defaults(
            locked,
            payload,
            metadata,
            environment_version,
            suite_version,
            pair,
            pieces=moved,
            outgoing_agent_version_id=locked.agent_version_id,
        )
        for version in targets.values():
            note_revision(version, pair.revision)
    else:
        store_set(locked, None)
    created = {"environment": created_environment, "suite": created_suite}
    for piece, draft in opened.items():
        discard_version(draft, user=user, saved_version_id=str(targets[piece].id))
    supersede_candidates(locked, keep={str(one.id) for one in targets.values()})
    return {piece: (targets[piece], created[piece]) for piece in opened}


def discard_expired_candidates(*, now: datetime | None = None) -> int:
    """Discard candidate sets nobody saved within `CANDIDATE_TTL`, each under its environment's lock. A candidate the
    environment's live set still holds, validated within the period, is kept however old the row is.
    """
    cutoff = (now or timezone.now()) - CANDIDATE_TTL
    environments = set()
    for piece in DRAFT_PIECES:
        environments |= set(
            piece_model(piece)
            .no_workspace_objects.filter(
                status=VersionStatus.CANDIDATE, created_at__lt=cutoff
            )
            .values_list("job_id", flat=True)
        )
    # A live set can name only versions that were already active (nothing new to build), so no candidate row finds it.
    environments |= {
        environment_id
        for environment_id, entry in HostedHarnessJob.no_workspace_objects.filter(
            **{f"payload__metadata__{VALIDATION_KEY}__isnull": False}
        ).values_list("id", f"payload__metadata__{VALIDATION_KEY}")
        if isinstance(entry, dict) and str(entry.get("at") or "") < cutoff.isoformat()
    }
    expired = 0
    for environment_id in environments:
        with transaction.atomic():
            locked = (
                HostedHarnessJob.no_workspace_objects.select_for_update()
                .filter(id=environment_id)
                .first()
            )
            if locked is None:
                continue
            entry = live_set(locked)
            keep: set[str] = set()
            if entry is not None and datetime.fromisoformat(entry["at"]) >= cutoff:
                keep = {entry["environment_version_id"], entry["suite_version_id"]}
            elif entry is not None:
                store_set(locked, None)
            for piece in DRAFT_PIECES:
                expired += (
                    piece_model(piece)
                    .no_workspace_objects.filter(
                        job=locked,
                        status=VersionStatus.CANDIDATE,
                        created_at__lt=cutoff,
                    )
                    .exclude(id__in=list(keep))
                    .update(
                        status=VersionStatus.DISCARDED,
                        discard_reason=DiscardReason.EXPIRED,
                        updated_at=timezone.now(),
                    )
                )
    logger.info(
        "harness_candidates_expired", environments=len(environments), candidates=expired
    )
    return expired
