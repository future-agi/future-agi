"""Who fixes each readiness row (the builder chat, the user, or a re-check) and the
instruction a builder fix sends to the chat. Pure functions of the rows."""

from __future__ import annotations

from collections.abc import Mapping

from django.conf import settings

from simulate.services.harness_readiness.catalogue import (
    FixGroup,
    FixGroupsView,
    RowView,
)

# Reasons a sealed environment's chat can fix, and the instruction it gets. World
# tools, probes and reset need the environment rebuilt, so those rows are the user's.
# State stays here for its seed data: its one schema_and_seed check cannot tell a
# seed failure from a schema one, so the message says what to do for each.
_BUILDER: dict[str, str] = {
    "state_failed": (
        "The test world's schema and seed check failed: fix its seed data so the world"
        " is seeded and its state check passes. If the schema itself is wrong, change"
        " nothing and say the environment must be rebuilt."
    ),
}
# Fix types that only read the pairing again.
_RECHECK_TYPES = frozenset({"recheck", "agent_tools_unread"})


def _group(row: RowView, by_id: Mapping[str, RowView]) -> FixGroup | None:
    fix = row["fix"]
    if fix is None:
        return None
    if fix["type"] == "gate":
        gate = by_id.get(row["blocked_by"] or "")
        # A gate with nothing to fix itself (Unavailable) is the user's to decide.
        return (gate["fix_group"] if gate else None) or "needs_you"
    if fix["type"] in _RECHECK_TYPES:
        return "recheck"
    if row["reason_code"] in _BUILDER:
        return "builder"
    # The agent's own settings, keys or tools, and reasons that need a person to read.
    return "needs_you"


def _message(row: RowView, by_id: Mapping[str, RowView]) -> str | None:
    fix = row["fix"]
    if row["fix_group"] != "builder" or fix is None:
        return None
    if fix["type"] == "gate":
        gate = by_id.get(row["blocked_by"] or "")
        # The gate's own instruction: fixing it is what lets this row be checked.
        return gate["fix"].get("message") if gate and gate["fix"] else None
    return _BUILDER[row["reason_code"] or ""]


def annotate(rows: list[RowView]) -> None:
    """Set each row's ``fix_group`` and its fix's ``message``, in catalogue order so a
    gate's group is known before the rows waiting on it."""
    by_id: dict[str, RowView] = {}
    for row in rows:
        row["fix_group"] = _group(row, by_id)
        if row["fix"] is not None:
            row["fix"]["message"] = _message(row, by_id)
        by_id[row["id"]] = row


def fix_groups(rows: list[RowView]) -> FixGroupsView:
    """The rows of each group."""
    ids: dict[FixGroup, list[str]] = {"builder": [], "needs_you": [], "recheck": []}
    for row in rows:
        # A report cached before rows were grouped has no ``fix_group``.
        group = row.get("fix_group")
        if group is not None:
            ids[group].append(row["id"])
    return {
        "builder": {"rows": ids["builder"]},
        "needs_you": {"rows": ids["needs_you"]},
        "recheck": {"rows": ids["recheck"]},
    }


def builder_message(rows: list[RowView]) -> str:
    """The builder rows' messages in row order, each once, one per line."""
    messages = (
        row["fix"].get("message")
        for row in rows
        if row.get("fix_group") == "builder" and row["fix"] is not None
    )
    return "\n".join(dict.fromkeys(one for one in messages if one))


def enabled() -> bool:
    """Whether live reports carry ``fix_groups``: its presence turns on the grouped
    Ready to run panel."""
    from simulate.services.harness_versioning.versions import versioning_enabled

    return versioning_enabled() and bool(
        getattr(settings, "HARNESS_READINESS_FIX_ENABLED", False)
    )
