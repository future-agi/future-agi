"""The review of a draft: every change the open drafts make, the decision taken on each, and the checks a reviewed
draft passes before Validate or Save.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from simulate.services.harness_versioning import archives
from simulate.services.harness_versioning.draft_pieces import (
    DRAFT_PIECES,
    draft_changed,
    draft_content,
    open_draft,
    piece_files,
    prepare_tree,
    version_error,
)
from simulate.services.harness_versioning.references import ALL, affects, depends_on
from simulate.services.harness_versioning.review_compose import (
    ReviewRefused,
    compose_accepted,
    review_digest,
)
from simulate.services.harness_versioning.review_items import (
    compute_changes,
    graded,
    item_index,
)
from simulate.services.hosted_harness import HostedHarnessError

DECISIONS = ("accepted", "rejected")


def _decisions(draft: Any) -> dict[str, dict[str, Any]]:
    return dict(((draft.documents or {}).get("review") or {}).get("decisions") or {})


def any_decision(environment: Any) -> bool:
    """Whether anyone decided a change on the environment's open drafts."""
    return any(
        _decisions(draft)
        for draft in (open_draft(environment, piece) for piece in DRAFT_PIECES)
        if draft is not None
    )


def held_decisions(draft: Any, files: Mapping[str, bytes]) -> dict[str, str]:
    """The decisions on a draft that still hold, by item: each only while its item is as it was when decided."""
    items = item_index(files)
    return {
        item_id: entry["decision"]
        for item_id, entry in _decisions(draft).items()
        if item_id in items and entry.get("draft_digest") == items[item_id].digest
    }


def _piece_changes(environment: Any, piece: str, draft: Any) -> list[dict[str, Any]]:

    before = piece_files(environment, piece, draft.parent) if draft.parent_id else {}
    after = piece_files(environment, piece, draft)
    return [{**change, "piece": piece} for change in compute_changes(before, after)]


def _scenarios(environment: Any) -> dict[str, dict[str, Any]]:
    """The scenarios as the drafts would leave them (the suite draft, else the saved suite), by key."""

    version = open_draft(environment, "suite") or environment.suite_version
    if version is None:
        return {}
    files = piece_files(environment, "suite", version)
    return {
        item.key: item.value
        for item in item_index(files).values()
        if item.kind == "scenario"
    }


def draft_review(environment: Any) -> dict[str, Any]:
    """Every change the open drafts make, each with the decision taken on it.

    A decision holds only for the content it was taken on: once the draft changes that item again, the item is
    undecided until someone decides it again.
    """

    drafts: dict[str, dict[str, Any]] = {}
    changes: list[dict[str, Any]] = []
    decided: dict[str, str] = {}
    defaults: list[str] = []
    for piece in DRAFT_PIECES:
        draft = open_draft(environment, piece)
        if draft is None:
            continue
        drafts[piece] = {
            "id": str(draft.id),
            "revision": draft.revision,
            "parent_id": str(draft.parent_id or ""),
        }
        defaults.append(str(getattr(draft.parent, "hash", "") or ""))
        recorded = _decisions(draft)
        sources = (draft.documents or {}).get("provenance") or {}
        for change in _piece_changes(environment, piece, draft):
            change["sources"] = list(reversed(sources.get(change["id"]) or []))
            entry = recorded.get(change["id"]) or {}
            decision = (
                entry.get("decision")
                if entry.get("draft_digest") == change["draft_digest"]
                else None
            )
            change["decision"] = decision
            if decision:
                decided[change["id"]] = decision
            changes.append(change)
    scenarios = _scenarios(environment)
    for change in changes:
        change["affects"] = affects(change, scenarios)
        change["depends_on"] = depends_on(change, changes, scenarios)
        change["graded"] = graded(change)
        touched = sorted(scenarios) if change["affects"] == ALL else change["affects"]
        change["revalidate"] = touched if change["graded"] else []
    counts = {
        "total": len(changes),
        "accepted": sum(one["decision"] == "accepted" for one in changes),
        "rejected": sum(one["decision"] == "rejected" for one in changes),
    }
    counts["undecided"] = counts["total"] - counts["accepted"] - counts["rejected"]
    return {
        "drafts": drafts,
        "changes": changes,
        "counts": counts,
        "review_digest": review_digest("|".join(defaults), changes, decided),
        "can_save": bool(changes) and counts["undecided"] == 0,
    }


def record_decisions(
    environment: Any,
    decisions: list[dict[str, Any]],
    *,
    expected_revisions: dict[str, int],
    user: Any,
) -> dict[str, Any]:
    """Store accept or reject on changes, by id, by kind, or for every remaining one, and return the review.

    A decision is kept with the content it was taken on. A draft that moved since the person looked refuses the
    whole request (`draft_changed`), so nobody decides on content they did not see.
    """
    from django.db import transaction

    with transaction.atomic():
        current = {
            piece: open_draft(environment, piece, lock=True) for piece in DRAFT_PIECES
        }
        current = {
            piece: draft for piece, draft in current.items() if draft is not None
        }
        if not current:
            raise version_error("draft_not_found", "No unsaved changes", 404)
        for piece, draft in current.items():
            if expected_revisions.get(piece) != draft.revision:
                raise draft_changed(piece=piece)
        changes = [
            change
            for piece, draft in current.items()
            for change in _piece_changes(environment, piece, draft)
        ]
        by_id = {change["id"]: change for change in changes}
        for item in decisions:
            decision = item.get("decision")
            if decision not in DECISIONS:
                raise version_error(
                    "decision_invalid", "A decision is accepted or rejected", 400
                )
            if item.get("id"):
                if item["id"] not in by_id:
                    raise version_error(
                        "change_not_found",
                        "That change is not in the draft any more",
                        404,
                        id=item["id"],
                    )
                targets = [by_id[item["id"]]]
            elif item.get("kind"):
                targets = [
                    change for change in changes if change["kind"] == item["kind"]
                ]
            else:
                targets = changes
            for change in targets:
                draft = current[change["piece"]]
                recorded = _decisions(draft)
                if (
                    item.get("remaining")
                    and change["id"] in recorded
                    and recorded[change["id"]].get("draft_digest")
                    == change["draft_digest"]
                ):
                    continue
                recorded[change["id"]] = {
                    "decision": decision,
                    "draft_digest": change["draft_digest"],
                }
                review = {
                    **((draft.documents or {}).get("review") or {}),
                    "decisions": recorded,
                }
                draft.documents = {**(draft.documents or {}), "review": review}
        for draft in current.values():
            draft.last_edited_by = user
            draft.save(update_fields=["documents", "last_edited_by", "updated_at"])
    return draft_review(environment)


def review_stale() -> HostedHarnessError:

    return version_error(
        "review_stale",
        "The changes moved since you reviewed them; review them again",
        409,
    )


def check_review(
    review: Mapping[str, Any],
    expected_review_digest: str | None,
    *,
    digest_required: bool,
) -> None:
    """Refuse a review that cannot be saved as decided. Once any change is decided: the digest the person saw must be
    current (required when ``digest_required``), no change may be left undecided, and no accepted change may need a
    rejected one."""

    changes = review["changes"]
    decided = any(one["decision"] for one in changes)
    if expected_review_digest != review["review_digest"] and (
        expected_review_digest or (decided and digest_required)
    ):
        raise review_stale()
    if not decided:
        return
    refused = {one["id"] for one in changes if one["decision"] == "rejected"}
    needs = [
        (one["id"], other)
        for one in changes
        if one["decision"] == "accepted"
        for other in one.get("depends_on") or []
        if other in refused
    ]
    if needs:
        raise version_error(
            "change_depends_on_rejected",
            "An accepted change needs one you rejected; accept both or reject both",
            409,
            pairs=[{"change": change, "needs": other} for change, other in needs],
        )
    if undecided := [one["id"] for one in changes if not one["decision"]]:
        raise version_error(
            "review_undecided",
            "Every change needs a decision before Save.",
            409,
            changes=undecided,
        )


_KEPT_CHANGE_FIELDS = (
    "id",
    "kind",
    "key",
    "action",
    "summary",
    "decision",
    "affects",
    "graded",
    "revalidate",
)


def review_changes(review: Mapping[str, Any], piece: str) -> list[dict[str, Any]]:
    """What the person decided on one piece's changes, kept with the version it becomes, so its summary, its proofs
    and the run warning follow the approved changes. A change saved with nothing decided is saved as it stands, so it
    reads as accepted."""
    return [
        {
            **{name: one[name] for name in _KEPT_CHANGE_FIELDS},
            "decision": one["decision"] or "accepted",
        }
        for one in review["changes"]
        if one["piece"] == piece
    ]


def composed_files(
    locked: Any, piece: str, draft: Any, review: Mapping[str, Any]
) -> dict[str, bytes]:
    """A drafted piece's files with exactly the accepted changes: the saved version plus those changes when any of
    the piece's changes was rejected, else the draft's own files."""

    changes = [one for one in review["changes"] if one["piece"] == piece]
    if not any(one["decision"] == "rejected" for one in changes):
        return piece_files(locked, piece, draft)
    decisions = {
        one["id"]: one["decision"] for one in review["changes"] if one["decision"]
    }
    try:
        return compose_accepted(
            piece_files(locked, piece, draft.parent) if draft.parent_id else {},
            piece_files(locked, piece, draft),
            changes,
            decisions,
        )
    except ReviewRefused as refused:
        raise version_error(
            refused.code, str(refused), 409, changes=refused.changes
        ) from refused


def apply_review(
    locked: Any, pieces: set[str], *, expected_review_digest: str | None
) -> None:
    """Before a Save that cannot be validated makes drafts live: when the person decided any change, rewrite each
    draft with a rejected change as the saved version plus exactly the accepted changes, inside the Save's
    transaction. With no decision at all, Save keeps today's behaviour (the whole draft).
    """

    review = draft_review(locked)
    check_review(review, expected_review_digest, digest_required=False)
    if not any(one["decision"] for one in review["changes"]):
        return
    rejected = {
        one["piece"] for one in review["changes"] if one["decision"] == "rejected"
    }
    for piece in sorted(pieces):
        draft = open_draft(locked, piece, lock=True)
        kept = {
            "review": (draft.documents or {}).get("review"),
            "review_changes": review_changes(review, piece),
        }
        if piece not in rejected:
            draft.documents = {**(draft.documents or {}), **kept}
            draft.save(update_fields=["documents", "updated_at"])
            continue
        composed = composed_files(locked, piece, draft, review)
        other = "suite" if piece == "environment" else "environment"
        other_version = open_draft(locked, other) or getattr(locked, f"{other}_version")
        _, prepared = prepare_tree(
            locked, {**composed, **piece_files(locked, other, other_version)}
        )
        archives.put_archive(
            *(
                (prepared.environment_key, prepared.environment_body)
                if piece == "environment"
                else (prepared.suite_key, prepared.suite_body)
            )
        )
        values = draft_content(
            locked, piece, prepared, origin=draft.origin, default=draft.parent
        )
        values["documents"].update(kept)
        for name, value in values.items():
            setattr(draft, name, value)
        draft.save()
