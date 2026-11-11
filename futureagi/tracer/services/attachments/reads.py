"""Bounded, metadata-only enrichment for trace and Voice readers.

B10 implements AuthorizedAttachmentReader using current viewer/session and full
scope/parent authorization. This is deliberately independent of OTLP hints and
legacy recording URLs. No installed reader returns no attachment metadata.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .binding import Parent, Scope
from .references import validate_reference

MAX_PARENTS = 100
MAX_PER_PARENT = 64
STATES = frozenset(
    (
        "pending_upload",
        "importing",
        "validating",
        "ready",
        "failed",
        "rejected",
        "expired",
        "deleted",
    )
)


@dataclass(frozen=True)
class ReadTarget:
    scope: Scope
    parent: Parent


@dataclass(frozen=True)
class AuthorizedStatus:
    target: ReadTarget
    reference: dict
    state: str
    bound: bool
    capture_complete: bool
    expires_at: datetime


class AuthorizedAttachmentReader(Protocol):
    def list_authorized(
        self, viewer, targets: tuple[ReadTarget, ...], *, per_parent_limit: int
    ) -> list[AuthorizedStatus]:
        """Bounded batch, not one query per parent. Never fetch media.

        Reauthorize viewer + application session + full scope + exact parent per
        item against live latest versions, policy and deletion generation. Omit
        denied/missing foreign rows identically, including existence/count/labels.
        Do not reuse public/shared-trace access as attachment authority. Read
        metadata contains no filenames, object/source URLs, hashes or grants.
        """
        ...


def attachment_statuses(
    *, viewer, targets, reader: AuthorizedAttachmentReader | None, now: datetime
):
    """One batch returns safe per-parent states; the resolver mints playback later."""
    if reader is None or viewer is None:
        return {}
    targets = tuple(dict.fromkeys(targets))
    if len(targets) > MAX_PARENTS or now.tzinfo is None:
        raise ValueError("invalid_attachment_read_bound")
    if not targets:
        return {}
    rows = reader.list_authorized(viewer, targets, per_parent_limit=MAX_PER_PARENT)
    if len(rows) > len(targets) * MAX_PER_PARENT:
        raise ValueError("attachment_reader_exceeded_bound")
    allowed = set(targets)
    result = {}
    for row in rows:
        if (
            row.target not in allowed
            or row.expires_at <= now
            or row.state in ("expired", "deleted", "rejected")
        ):
            continue
        if row.state not in STATES:
            raise ValueError("invalid_attachment_status")
        ref = validate_reference(row.reference)
        items = result.setdefault(row.target, [])
        if len(items) >= MAX_PER_PARENT:
            raise ValueError("attachment_reader_exceeded_bound")
        status = "pending" if not row.bound else row.state
        if status == "ready" and not row.capture_complete:
            status = "partial"
        items.append(
            {
                "reference": ref,
                "status": status,
                "capture_complete": row.capture_complete,
            }
        )
    return result
