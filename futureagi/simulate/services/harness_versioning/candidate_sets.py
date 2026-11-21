"""The live candidate set of an environment: what the last Validate built and where its validation stands.

Validate composes each drafted piece's default plus exactly the accepted changes into candidate versions, and
certifies the (default agent, candidate environment, candidate suite) pairing without a run. The set is kept in the
environment's metadata under `draft_validation`, one per environment; a candidate is never a default and never
runnable until Save promotes it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NotRequired, TypedDict, cast

from django.utils import timezone

from simulate.models import HarnessAssembledBundle, HostedHarnessJob
from simulate.models.harness_versioning import DiscardReason, VersionStatus
from simulate.services.harness_scenario_needs import affected_by_tool, case_needs
from simulate.services.harness_versioning import validation
from simulate.services.harness_versioning.draft_pieces import DRAFT_PIECES, piece_model

VALIDATION_KEY = "draft_validation"


class Defaults(TypedDict):
    agent: str
    environment: str
    suite: str


class CandidateDraft(TypedDict):
    id: str
    revision: int


class FailedValidation(TypedDict):
    code: str
    message: str
    # Each action whose check failed, with the reason, as the bundle recorded it.
    failed_actions: list[dict[str, Any]]


class CandidateValidation(TypedDict):
    """Where the live candidate set's validation stands, as the drafts and Validate answers show it."""

    state: str
    job_id: str | None
    error: FailedValidation | None
    affected_scenarios: list[str]
    dropped_scenarios: list[str]


class CandidateSet(TypedDict):
    """What the last Validate built, as the environment's metadata keeps it."""

    environment_version_id: str
    suite_version_id: str
    agent_version_id: str
    assembly_key: str
    base_defaults: Defaults
    # Per piece with a draft: the draft and the revision the candidates were built from.
    drafts: dict[str, CandidateDraft]
    review_digest: str
    # The decisions the drafts kept at Validate, so a read checks them without building the review.
    decisions_digest: NotRequired[str]
    job_id: str | None
    at: str
    # The check ended on our side for a cause that repeats: the set may be saved uncertified, as a run may go ahead.


def validation_available(environment: HostedHarnessJob) -> bool:
    """Whether the environment's pairings can be certified with its default agent."""
    return validation.can_validate(environment, environment.agent_version_id)


def current_defaults(environment: HostedHarnessJob) -> Defaults:
    return {
        "agent": str(environment.agent_version_id or ""),
        "environment": str(environment.environment_version_id or ""),
        "suite": str(environment.suite_version_id or ""),
    }


_CANDIDATE_SET_KEYS = CandidateSet.__required_keys__


def live_set(environment: HostedHarnessJob) -> CandidateSet | None:
    """The live candidate set the environment keeps, read into its shape; one missing a field reads as none, so a
    Save asks for Validate again rather than act on part of a set."""
    entry = ((environment.payload or {}).get("metadata") or {}).get(VALIDATION_KEY)
    if not isinstance(entry, dict) or not _CANDIDATE_SET_KEYS <= set(entry):
        return None
    return cast(CandidateSet, entry)


def store_set(locked: HostedHarnessJob, entry: CandidateSet | None) -> None:
    """Keep ``entry`` as the live set, or drop it; the caller holds the environment lock."""
    payload = dict(locked.payload or {})
    metadata = dict(payload.get("metadata") or {})
    if entry is None:
        metadata.pop(VALIDATION_KEY, None)
    else:
        metadata[VALIDATION_KEY] = entry
    payload["metadata"] = metadata
    locked.payload = payload
    locked.save(update_fields=["payload", "updated_at"])


def supersede_candidates(
    environment: HostedHarnessJob, *, keep: set[str] | frozenset[str] = frozenset()
) -> None:
    """Discard the environment's candidates other than ``keep``."""
    for piece in DRAFT_PIECES:
        piece_model(piece).no_workspace_objects.filter(
            job=environment, status=VersionStatus.CANDIDATE
        ).exclude(id__in=list(keep)).update(
            status=VersionStatus.DISCARDED,
            discard_reason=DiscardReason.SUPERSEDED,
            updated_at=timezone.now(),
        )


def discard_candidates(locked: HostedHarnessJob) -> None:
    """Drop the live set and its candidates: the drafts it was built from are gone. The caller holds the lock."""
    if live_set(locked) is None:
        return
    supersede_candidates(locked)
    store_set(locked, None)


def version_id_of(entry: CandidateSet, piece: str) -> str:
    """The candidate version the set holds for a draft piece."""
    return (
        entry["environment_version_id"]
        if piece == "environment"
        else entry["suite_version_id"]
    )


def set_versions(environment: HostedHarnessJob, entry: CandidateSet) -> dict[str, Any]:
    return {
        piece: piece_model(piece)._base_manager.get(
            job=environment, id=version_id_of(entry, piece)
        )
        for piece in DRAFT_PIECES
    }


def set_bundle(
    environment: HostedHarnessJob, entry: CandidateSet
) -> HarnessAssembledBundle | None:
    return HarnessAssembledBundle._base_manager.filter(
        job_id=environment.id, assembly_key=entry["assembly_key"]
    ).first()


def unavailable_on_our_side(entry: CandidateSet, bundle: Any) -> bool:
    """Whether this set's check ended on our side for a cause that would end a new check the same way: read from the
    pairing's own last attempt, so the person never has to Validate a second time only to learn it."""
    return bool(bundle is not None and validation.repeats_unavailable(bundle))


def validation_state(
    environment: HostedHarnessJob, entry: CandidateSet | None = None
) -> CandidateValidation | None:
    """Where the live set's validation stands: `validating`, `retry` (a job ended before any check ran), `ready`,
    `failed`, `check_unavailable` (the check ended on our side for a cause that repeats; the set may save
    uncertified), or `superseded` once the defaults moved under it, or once the drafts or decisions moved since the set
    was validated (Save would refuse it). A failed validation names the actions that failed and
    the scenarios that need one of them on every path; a ready one names the scenarios certifying dropped.
    """
    entry = entry or live_set(environment)
    if entry is None:
        return None
    bundle = set_bundle(environment, entry)
    state = "validating"
    if bundle is not None and bundle.state == HarnessAssembledBundle.State.PENDING:
        if validation.live_validation(bundle.id) is None:
            state = "retry"
    if current_defaults(environment) != entry["base_defaults"]:
        state = "superseded"
    elif bundle is not None and bundle.state == HarnessAssembledBundle.State.READY:
        state = "ready"
    elif bundle is not None and bundle.state == HarnessAssembledBundle.State.FAILED:
        state = "failed"
    elif unavailable_on_our_side(entry, bundle):
        state = "check_unavailable"
    if state in ("ready", "check_unavailable") and _drafts_moved(environment, entry):
        # Save would refuse these candidates: what it would save is no longer what was validated.
        state = "superseded"
    body = CandidateValidation(
        state=state,
        job_id=entry.get("job_id"),
        error=None,
        affected_scenarios=[],
        dropped_scenarios=[],
    )
    if state == "ready" and bundle is not None:
        body["dropped_scenarios"] = sorted(
            {
                str(key)
                for warning in bundle.warnings or []
                if isinstance(warning, dict)
                and warning.get("code") == "scenario_dropped"
                for key in warning.get("keys") or []
            }
        )
    if state == "failed" and bundle is not None:
        # Imported here: readiness reaches the drafts, which import this module.
        from simulate.services.harness_readiness.rules import failed_actions

        error = bundle.error if isinstance(bundle.error, dict) else {}
        failed = [dict(one) for one in failed_actions(bundle)]
        body["error"] = FailedValidation(
            code=str(error.get("code") or ""),
            message=str(error.get("message") or ""),
            failed_actions=failed,
        )
        body["affected_scenarios"] = _affected_scenarios(
            environment, entry, {str(one["action"]) for one in failed}
        )
    return body


def _drafts_moved(environment: HostedHarnessJob, entry: CandidateSet) -> bool:
    """Whether the drafts or the review decisions moved since this set was validated, as Save checks them. Cheap on
    every drafts read: the drafts' ids and revisions, then the decisions they keep; a set stored before it kept its
    decisions is checked against the whole review."""
    from simulate.services.harness_versioning import review
    from simulate.services.harness_versioning.draft_pieces import open_draft

    current = {}
    for piece, seen in entry["drafts"].items():
        draft = open_draft(environment, piece)
        if (
            draft is None
            or str(draft.id) != seen["id"]
            or draft.revision != seen["revision"]
        ):
            return True
        current[piece] = draft
    # A piece opened since, such as a chat turn's environment change, is a change Validate did not see.
    if any(
        piece not in entry["drafts"] and open_draft(environment, piece) is not None
        for piece in DRAFT_PIECES
    ):
        return True
    if "decisions_digest" in entry:
        return review.decisions_digest(current) != entry["decisions_digest"]
    return review.draft_review(environment)["review_digest"] != entry["review_digest"]


def _affected_scenarios(
    environment: HostedHarnessJob, entry: CandidateSet, tools: set[str]
) -> list[str]:
    """The candidate suite's scenarios a failed tool affects: those that need it on every path."""
    versions = set_versions(environment, entry)
    return [
        case.key
        for case in case_needs(
            environment_version=versions["environment"], suite_version=versions["suite"]
        )
        if any(affected_by_tool(case, tool) for tool in tools)
    ]


def candidate_states(environment: HostedHarnessJob) -> Mapping[str, str]:
    """Per version id of the live set, the state its History label reads."""
    entry = live_set(environment)
    if entry is None:
        return {}
    current = validation_state(environment, entry)
    state = current["state"] if current is not None else "validating"
    return {entry["environment_version_id"]: state, entry["suite_version_id"]: state}
