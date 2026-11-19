"""Deterministic evidence samples; authoritative membership stays in PostgreSQL."""

import re
from types import SimpleNamespace

from tracer.queries.grouping import canonical_snapshot_digest

MAX_ISSUE_EVIDENCE_MEMBERS = 8


def sample_issue_metadata(rows, prototype_ids):
    """Select IDs without hydrating every finding and report model."""
    members = [
        SimpleNamespace(
            id=row["id"],
            statement=row["statement"],
            report=SimpleNamespace(recorded_at=row["report__recorded_at"]),
        )
        for row in rows
    ]
    return [str(item.id) for item in sample_issue_members(members, prototype_ids)]


def sample_issue_members(members, prototype_ids):
    """Keep prototypes, a recent example, then lexically diverse boundaries.

    Lexical diversity is a cheap selection cue, never a duplicate decision.
    Neither this selection nor a positive model review certifies unseen calls.
    Stable ordering lets publication reconstruct the exact claimed read set.
    """
    by_id = {str(item.id): item for item in members}
    selected = [by_id[key] for key in prototype_ids if key in by_id]
    if (
        not prototype_ids
        or len(selected) != len(set(prototype_ids))
        or len(selected) > 5
    ):
        raise ValueError("issue prototypes are not current distinct members")
    if not members:
        return []
    latest = max(members, key=lambda item: (item.report.recorded_at, str(item.id)))
    if latest not in selected:
        selected.append(latest)
    tokens = {
        str(item.id): set(re.findall(r"\w+", item.statement.casefold()))
        for item in members
    }

    def similarity(first, second):
        a, b = tokens[str(first.id)], tokens[str(second.id)]
        return len(a & b) / max(1, len(a | b))

    while len(selected) < min(MAX_ISSUE_EVIDENCE_MEMBERS, len(members)):
        remaining = [item for item in members if item not in selected]
        selected.append(
            min(
                remaining,
                key=lambda item: (
                    max((similarity(item, peer) for peer in selected), default=0),
                    str(item.id),
                ),
            )
        )
    return sorted(selected, key=lambda item: str(item.id))


def membership_binding(state, member_ids):
    return {
        "member_count": len(member_ids),
        "membership_revision": state.membership_revision,
        "membership_digest": canonical_snapshot_digest(
            {"member_ids": sorted(member_ids)}
        ),
        "evidence_mode": "sampled",
    }
