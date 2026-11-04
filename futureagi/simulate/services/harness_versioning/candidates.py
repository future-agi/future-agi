"""Validate, then Save: candidate versions built from a reviewed draft, certified without a run, then promoted.

Validate composes each piece's default plus exactly the accepted changes into candidate versions and starts a
validation of the (default agent, candidate environment, candidate suite) pairing. A candidate is never a default and
never runnable, and the draft is not written, so a failed validation leaves the draft as the person left it. Save
promotes the candidates only once that pairing is certified, and only while the defaults and the drafts are still the
ones the candidates were built from.

The live candidate set is kept in the environment's metadata under `draft_validation`, one per environment.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any, TypedDict

import structlog
from django.db import transaction
from django.utils import timezone

from simulate.models import AgentVersion, HarnessAssembledBundle, HostedHarnessJob
from simulate.models.harness_versioning import DiscardReason, HashKind, VersionStatus
from simulate.services.harness_versioning import archives, drafts, review, validation
from simulate.services.harness_versioning.versions import (
    _add_members,
    _default_pair,
    _lock_environment,
    _member_rows,
    _next_number,
    note_revision,
    require_not_busy,
    set_defaults,
)
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

VALIDATION_KEY = "draft_validation"
# How long an unpromoted candidate is kept before the daily sweep discards it.
CANDIDATE_TTL = timedelta(days=7)


class Defaults(TypedDict):
    agent: str
    environment: str
    suite: str


class CandidateSet(TypedDict):
    """What one Validate built, as the environment's metadata keeps it."""

    environment_version_id: str
    suite_version_id: str
    agent_version_id: str
    assembly_key: str
    base_defaults: Defaults
    # Per piece with a draft: the draft and the revision the candidates were built from.
    drafts: dict[str, dict[str, Any]]
    review_digest: str
    job_id: str | None
    at: str


def validation_available(environment: HostedHarnessJob) -> bool:
    """Whether the environment's pairings can be certified with its default agent."""
    return validation.can_validate(environment, environment.agent_version_id)


def any_decision(environment: HostedHarnessJob) -> bool:
    """Whether anyone decided a change on the open drafts."""
    return any(
        (((draft.documents or {}).get("review") or {}).get("decisions"))
        for draft in (
            drafts.open_draft(environment, piece) for piece in drafts.DRAFT_PIECES
        )
        if draft is not None
    )


def _defaults(environment: HostedHarnessJob) -> Defaults:
    return {
        "agent": str(environment.agent_version_id or ""),
        "environment": str(environment.environment_version_id or ""),
        "suite": str(environment.suite_version_id or ""),
    }


def live_set(environment: HostedHarnessJob) -> CandidateSet | None:
    entry = ((environment.payload or {}).get("metadata") or {}).get(VALIDATION_KEY)
    return entry if isinstance(entry, dict) else None


def _bundle(
    environment: HostedHarnessJob, entry: CandidateSet
) -> HarnessAssembledBundle | None:
    return HarnessAssembledBundle._base_manager.filter(
        job_id=environment.id, assembly_key=entry["assembly_key"]
    ).first()


def _set_versions(environment: HostedHarnessJob, entry: CandidateSet) -> dict[str, Any]:
    return {
        piece: drafts._model(piece)._base_manager.get(
            job=environment, id=entry[f"{piece}_version_id"]
        )
        for piece in drafts.DRAFT_PIECES
    }


def validation_state(
    environment: HostedHarnessJob, entry: CandidateSet | None = None
) -> dict[str, Any] | None:
    """Where the live candidate set's validation stands: `validating`, `ready`, `failed`, or `superseded` once the
    defaults moved under it. A failed validation names the actions that failed and the scenarios that need them on
    every path; a ready one names the scenarios certifying dropped."""
    entry = entry or live_set(environment)
    if entry is None:
        return None
    bundle = _bundle(environment, entry)
    state = "validating"
    if bundle is not None and bundle.state == HarnessAssembledBundle.State.PENDING:
        # Infrastructure ended the last job before a check ran: the pairing is not failed, it is checked again.
        if validation.live_validation(bundle.id) is None:
            state = "retry"
    if _defaults(environment) != entry["base_defaults"]:
        state = "superseded"
    elif bundle is not None and bundle.state == HarnessAssembledBundle.State.READY:
        state = "ready"
    elif bundle is not None and bundle.state == HarnessAssembledBundle.State.FAILED:
        state = "failed"
    body: dict[str, Any] = {
        "state": state,
        "job_id": entry.get("job_id"),
        "error": None,
        "affected_scenarios": [],
        "dropped_scenarios": [],
    }
    if state == "ready":
        body["dropped_scenarios"] = sorted(
            {
                str(key)
                for warning in bundle.warnings or []
                if isinstance(warning, dict)
                and warning.get("code") == "scenario_dropped"
                for key in warning.get("keys") or []
            }
        )
    if state == "failed":
        error = bundle.error if isinstance(bundle.error, dict) else {}
        failed = [
            one for one in error.get("failed_actions") or [] if isinstance(one, dict)
        ]
        body["error"] = {
            "code": str(error.get("code") or ""),
            "message": str(error.get("message") or ""),
            "failed_actions": failed,
        }
        suite = _set_versions(environment, entry)["suite"]
        scenarios = {
            str(one.get("scenario_key") or one.get("name")): one
            for one in (suite.documents or {}).get("scenarios") or []
            if isinstance(one, dict)
        }
        body["affected_scenarios"] = _affected_scenarios(
            {str(one.get("action")) for one in failed}, scenarios
        )
    return body


def _affected_scenarios(
    tools: set[str], scenarios: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """The scenarios a failed tool breaks, by TH-8881's one definition of affected (the scenario needs the tool on
    every path). That definition lands with TH-8881's branch; until it is merged here, none are named.
    """
    return []


def candidate_states(environment: HostedHarnessJob) -> dict[str, str]:
    """Per candidate version id of the live set, the state its History label reads."""
    entry = live_set(environment)
    if entry is None:
        return {}
    state = (validation_state(environment, entry) or {}).get("state", "validating")
    return {entry["environment_version_id"]: state, entry["suite_version_id"]: state}


def _supersede(environment: HostedHarnessJob, *, keep: set[str] = frozenset()) -> None:
    """Discard the environment's candidates other than ``keep``."""
    for piece in drafts.DRAFT_PIECES:
        drafts._model(piece).no_workspace_objects.filter(
            job=environment, status=VersionStatus.CANDIDATE
        ).exclude(id__in=list(keep)).update(
            status=VersionStatus.DISCARDED,
            discard_reason=DiscardReason.SUPERSEDED,
            updated_at=timezone.now(),
        )


def _store(locked: HostedHarnessJob, entry: CandidateSet | None) -> None:
    payload = dict(locked.payload or {})
    metadata = dict(payload.get("metadata") or {})
    if entry is None:
        metadata.pop(VALIDATION_KEY, None)
    else:
        metadata[VALIDATION_KEY] = entry
    payload["metadata"] = metadata
    locked.payload = payload
    locked.save(update_fields=["payload", "updated_at"])


def discard_candidates(locked: HostedHarnessJob) -> None:
    """Drop the live candidate set: the drafts it was built from were discarded. The caller holds the lock."""
    if live_set(locked) is None:
        return
    _supersede(locked)
    _store(locked, None)


# --- Validate ---------------------------------------------------------------------------------------------------


def _open_drafts(locked: HostedHarnessJob) -> dict[str, Any]:
    opened = {
        piece: drafts.open_draft(locked, piece, lock=True)
        for piece in drafts.DRAFT_PIECES
    }
    opened = {piece: draft for piece, draft in opened.items() if draft is not None}
    if not opened:
        raise drafts._error("draft_not_found", "No unsaved changes", 404)
    return opened


def _require_fresh(locked: HostedHarnessJob, opened: Mapping[str, Any]) -> None:
    """The checks today's Save makes before anything moves: no stale draft, and no retired or taken scenario key."""
    for piece, draft in opened.items():
        if drafts.is_stale(draft, locked):
            raise drafts._error(
                "draft_stale",
                "The environment changed since you started editing; discard or re-apply in chat",
                409,
                piece=piece,
            )
        if piece == "suite":
            drafts._refuse(
                locked,
                (draft.documents or {}).get("scenarios") or [],
                origin="manual",
                exempt=drafts._exempt(draft),
            )


def _draft_changed(piece: str) -> HostedHarnessError:
    return drafts._error(
        "draft_changed",
        "Someone changed these edits a moment ago; review them and try again",
        409,
        piece=piece,
    )


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
    built from the same defaults, or a new candidate."""
    model = drafts._model(piece)
    content_hash = (
        prepared.environment_hash if piece == "environment" else prepared.suite_hash
    )
    twin = (
        model.no_workspace_objects.filter(
            job=locked,
            hash=content_hash,
            hash_kind=HashKind.TREE_V1,
            status=VersionStatus.ACTIVE,
        )
        .order_by("-version_number")
        .first()
    )
    if twin is not None:
        return twin
    live = model.no_workspace_objects.filter(
        job=locked,
        status=VersionStatus.CANDIDATE,
        hash=content_hash,
        documents__base_defaults=documents["base_defaults"],
    ).first()
    if live is not None:
        return live
    body = (
        (prepared.environment_key, prepared.environment_body)
        if piece == "environment"
        else (prepared.suite_key, prepared.suite_body)
    )
    archives.put_archive(*body)
    current = getattr(locked, f"{piece}_version")
    values = drafts._content(
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
    reviewed: Mapping[str, Any],
    *,
    user: Any,
) -> dict[str, Any]:
    """Both pieces of the pairing to validate: each drafted piece's default plus exactly its accepted changes (its
    draft's own files when it has no rejected change), the other piece's default. The drafts are not written.
    """
    base_defaults = _defaults(locked)
    files = {}
    for piece in drafts.DRAFT_PIECES:
        draft = opened.get(piece)
        files[piece] = (
            review.composed_files(locked, piece, draft, reviewed)
            if draft is not None
            else drafts._piece_files(locked, piece, getattr(locked, f"{piece}_version"))
        )
    _, prepared = drafts._prepared(locked, {**files["environment"], **files["suite"]})
    revisions = {piece: draft.revision for piece, draft in opened.items()}
    versions = {}
    for piece in drafts.DRAFT_PIECES:
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


def _start_validation(
    locked: HostedHarnessJob, versions: Mapping[str, Any]
) -> validation.Validation:
    """Certify the default agent with the candidate pairing: already ready, a validation still running, or a new
    run-less job (a failed pairing is checked again)."""
    started = validation.start_validation(
        locked,
        AgentVersion.no_workspace_objects.get(id=locked.agent_version_id),
        versions["environment"],
        versions["suite"],
        purpose="save",
    )
    if started is None:
        # `validation_available` was checked under the same lock.
        raise RuntimeError("pairing validation became unavailable under the lock")
    return started


def _same_set(
    entry: CandidateSet | None, versions: Mapping[str, Any], base_defaults: Defaults
) -> bool:
    return (
        entry is not None
        and entry["environment_version_id"] == str(versions["environment"].id)
        and entry["suite_version_id"] == str(versions["suite"].id)
        and entry["base_defaults"] == base_defaults
    )


def validate_drafts(
    environment: HostedHarnessJob,
    *,
    expected_revisions: Mapping[str, int],
    expected_review_digest: str | None,
    user: Any,
) -> tuple[dict[str, Any], bool]:
    """Build candidates from the open drafts and the decisions on them, and start validating them.

    Returns the body and whether a validation job is running (202) rather than already settled (200). Refuses as
    today's Save does (`chat_working`, `environment_busy`, `rebuild_running`, `draft_stale`, retired or taken keys),
    then a review that moved (`review_stale`), an undecided change once any is decided (`review_undecided`), an
    accepted change that needs a rejected one (`change_depends_on_rejected`) and a draft that moved since the
    person read it (`draft_changed`).
    """
    with transaction.atomic():
        locked = _lock_environment(environment)
        opened = _open_drafts(locked)
        first = next(iter(opened))
        require_not_busy(locked, first)
        drafts.require_no_rebuild(locked, piece=first)
        drafts.require_chat_idle(locked)
        _require_fresh(locked, opened)
        reviewed = review.draft_review(locked)
        review.check_review(reviewed, expected_review_digest, digest_required=True)
        for piece, draft in opened.items():
            if expected_revisions.get(piece) != draft.revision:
                raise _draft_changed(piece)
        if not validation_available(locked):
            return {"validation": {"state": "unavailable"}, "candidates": None}, False
        base_defaults = _defaults(locked)
        versions = create_candidates(locked, opened, reviewed, user=user)
        entry = live_set(locked)
        started = _start_validation(locked, versions)
        job_id = str(started.job.id) if started.job is not None else None
        if not _same_set(entry, versions, base_defaults):
            _supersede(locked, keep={str(one.id) for one in versions.values()})
            entry = {
                "environment_version_id": str(versions["environment"].id),
                "suite_version_id": str(versions["suite"].id),
                "agent_version_id": base_defaults["agent"],
                "assembly_key": started.bundle.assembly_key,
                "base_defaults": base_defaults,
                "drafts": {
                    piece: {"id": str(draft.id), "revision": draft.revision}
                    for piece, draft in opened.items()
                },
                "review_digest": reviewed["review_digest"],
                "job_id": job_id,
                "at": timezone.now().isoformat(),
            }
            _store(locked, entry)
        elif entry["job_id"] != job_id:
            # The same set validated again: a failed or interrupted pairing is checked by a new job.
            entry = {**entry, "job_id": job_id}
            _store(locked, entry)
        state = validation_state(locked, entry)
    logger.info(
        "harness_drafts_validated",
        environment_id=str(locked.id),
        environment_version_id=entry["environment_version_id"],
        suite_version_id=entry["suite_version_id"],
        state=state["state"],
        job_id=entry["job_id"],
    )
    body = {
        "validation": state,
        "candidates": {
            "environment_version_id": entry["environment_version_id"],
            "suite_version_id": entry["suite_version_id"],
        },
    }
    return body, state["state"] == "validating"


# --- Save ---------------------------------------------------------------------------------------------------------


def _not_validated() -> HostedHarnessError:
    return drafts._error(
        "not_validated",
        "These changes haven't passed validation; validate them, then save",
        409,
    )


def _promote(
    locked: HostedHarnessJob, piece: str, version: Any, *, environment_version: Any
) -> tuple[Any, bool]:
    """A candidate becomes an active version numbered next; an active version with its content, if one appeared
    meanwhile, is used instead."""
    if version.status != VersionStatus.CANDIDATE:
        return version, False
    model = drafts._model(piece)
    twin = drafts._twin(model, locked, version)
    if twin is not None:
        version.status, version.discard_reason = (
            VersionStatus.DISCARDED,
            DiscardReason.SUPERSEDED,
        )
        version.save(update_fields=["status", "discard_reason", "updated_at"])
        return twin, False
    version.status = VersionStatus.ACTIVE
    version.version_number = _next_number(model, locked)
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
    moved_defaults = False
    with transaction.atomic():
        locked = _lock_environment(environment)
        require_not_busy(locked, "environment")
        drafts.require_no_rebuild(locked, piece="environment")
        drafts.require_chat_idle(locked)
        replayed = drafts.replayed_save(locked, idempotency_key)
        if replayed is not None:
            return replayed
        entry = live_set(locked)
        if entry is None or any(
            str(candidates.get(f"{piece}_version_id") or "")
            != entry[f"{piece}_version_id"]
            for piece in drafts.DRAFT_PIECES
        ):
            raise _not_validated()
        if _defaults(locked) != entry["base_defaults"]:
            _supersede(locked)
            _store(locked, None)
            moved_defaults = True
        else:
            activated = _promote_set(
                locked, entry, expected_review_digest=expected_review_digest, user=user
            )
            review.name_saved(activated, name=name)
            drafts.remember_save(locked, idempotency_key, activated)
            transaction.on_commit(lambda: drafts.retire_chat_runtime(locked))
    if moved_defaults:
        raise drafts._error(
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
    return drafts._saved_body(
        locked, {piece: version for piece, (version, _) in activated.items()}
    )


def _promote_set(
    locked: HostedHarnessJob,
    entry: CandidateSet,
    *,
    expected_review_digest: str | None,
    user: Any,
) -> dict[str, tuple[Any, bool]]:
    bundle = _bundle(locked, entry)
    if bundle is None or bundle.state != HarnessAssembledBundle.State.READY:
        raise _not_validated()
    opened = {
        piece: drafts.open_draft(locked, piece, lock=True) for piece in entry["drafts"]
    }
    for piece, seen in entry["drafts"].items():
        draft = opened[piece]
        if (
            draft is None
            or str(draft.id) != seen["id"]
            or draft.revision != seen["revision"]
        ):
            raise _draft_changed(piece)
    if expected_review_digest and expected_review_digest != entry["review_digest"]:
        raise review.review_stale()
    if review.draft_review(locked)["review_digest"] != entry["review_digest"]:
        raise review.review_stale()
    built = _set_versions(locked, entry)
    environment_version, created_environment = _promote(
        locked, "environment", built["environment"], environment_version=None
    )
    suite_version, created_suite = _promote(
        locked, "suite", built["suite"], environment_version=environment_version
    )
    if created_suite:
        # Paired while the old suite is still the default, as a recorded save pairs.
        _add_members(
            suite_version,
            _member_rows(
                locked, (suite_version.documents or {}).get("scenarios") or []
            ),
        )
    targets = {"environment": environment_version, "suite": suite_version}
    moved = {
        piece
        for piece in drafts.DRAFT_PIECES
        if targets[piece].id != getattr(locked, f"{piece}_version_id")
    }
    if moved:
        payload = dict(locked.payload or {})
        metadata = dict(payload.get("metadata") or {})
        metadata.pop(VALIDATION_KEY, None)
        pair = _default_pair(
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
        _store(locked, None)
    created = {"environment": created_environment, "suite": created_suite}
    for piece, draft in opened.items():
        drafts._discard(draft, user=user, saved_version_id=str(targets[piece].id))
    _supersede(locked, keep={str(one.id) for one in targets.values()})
    return {piece: (targets[piece], created[piece]) for piece in opened}


# --- Cleanup --------------------------------------------------------------------------------------------------------


def discard_expired_candidates(*, now: Any = None) -> int:
    """Discard candidates nobody promoted within `CANDIDATE_TTL`, with the environments' live sets that held them."""
    cutoff = (now or timezone.now()) - CANDIDATE_TTL
    environments = set()
    for piece in drafts.DRAFT_PIECES:
        expired = drafts._model(piece).no_workspace_objects.filter(
            status=VersionStatus.CANDIDATE, created_at__lt=cutoff
        )
        environments |= set(expired.values_list("job_id", flat=True))
        expired.update(
            status=VersionStatus.DISCARDED,
            discard_reason=DiscardReason.EXPIRED,
            updated_at=timezone.now(),
        )
    for environment_id in environments:
        with transaction.atomic():
            locked = (
                HostedHarnessJob.no_workspace_objects.select_for_update()
                .filter(id=environment_id)
                .first()
            )
            entry = live_set(locked) if locked is not None else None
            if entry is not None and entry["at"] < cutoff.isoformat():
                _store(locked, None)
    logger.info("harness_candidates_expired", environments=len(environments))
    return len(environments)
