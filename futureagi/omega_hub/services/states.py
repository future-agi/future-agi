"""The submission state machine (SPEC §3.1). Every move goes through `move`, which refuses any edge
not in the table, so there is no path to `approved` except a maintainer's decision."""

from __future__ import annotations

from omega_hub.errors import HubError
from omega_hub.models import Submission

S = Submission.Status

ALLOWED: dict[str, set[str]] = {
    S.RECEIVED: {S.CHECKING, S.WITHDRAWN, S.SUPERSEDED},
    S.CHECKING: {S.CHECKS_FAILED, S.CHECK_ERROR, S.IN_REVIEW, S.RECEIVED, S.WITHDRAWN, S.SUPERSEDED},
    S.IN_REVIEW: {S.APPROVED, S.CHANGES_REQUESTED, S.REJECTED, S.WITHDRAWN, S.SUPERSEDED, S.RECEIVED},
    S.CHECK_ERROR: {S.RECEIVED},
    S.CHECKS_FAILED: set(),
    S.APPROVED: set(),
    S.CHANGES_REQUESTED: set(),
    S.REJECTED: set(),
    S.WITHDRAWN: set(),
    S.SUPERSEDED: set(),
}
OPEN = (S.RECEIVED, S.CHECKING, S.IN_REVIEW)
TERMINAL = (S.CHECKS_FAILED, S.APPROVED, S.CHANGES_REQUESTED, S.REJECTED, S.WITHDRAWN, S.SUPERSEDED)
# `approved` is reachable only from services.review.decide; this guard keeps it that way.
_APPROVE_TOKEN = object()


def can_move(current: str, target: str) -> bool:
    return target in ALLOWED.get(current, set())


def move(submission: Submission, target: str, *, reason: str = "", _approve=None) -> Submission:
    if target == S.APPROVED and _approve is not _APPROVE_TOKEN:
        raise HubError("OMEGA_HUB_BAD_STATE", 409, "only a maintainer's approve decision publishes")
    if not can_move(submission.status, target):
        raise HubError("OMEGA_HUB_BAD_STATE", 409, f"cannot move from {submission.status} to {target}")
    submission.status = target
    submission.status_reason = reason
    submission.save(update_fields=["status", "status_reason", "updated_at"])
    return submission
