"""Who fixes each readiness row (the builder chat, the user, or a re-check) and the
instruction a builder fix sends to the chat. Pure functions of the rows."""

from __future__ import annotations

from collections.abc import Mapping

from django.conf import settings

from simulate.services.harness_readiness.catalogue import (
    FixGroup,
    FixGroupsView,
    Reading,
    RowView,
    Target,
)
from simulate.services.harness_readiness.findings import StructuralFinding

# Reasons the environment itself can change: the target kind the message names, its
# instruction to the builder chat (``{target}`` is those names), and what to change
# when the row names none (the message then starts with the row's reason).
_BUILDER: dict[str, tuple[str, str, str]] = {
    "tool_not_in_world": (
        "agent_tool",
        "The agent calls {target}, which this environment does not have: add"
        " {target} to the test world so the agent's calls land there and its probe"
        " passes.",
        "add the tools the agent calls to the test world.",
    ),
    "tool_signature_changed": (
        "tool",
        "The agent calls {target} with other arguments than this environment takes:"
        " change {target} in the test world to take the arguments the agent sends.",
        "change those tools in the test world to take the arguments the agent sends.",
    ),
    "tool_unreachable": (
        "tool",
        "{target} has no reachable implementation in the test world: give {target}"
        " one there that the agent's calls reach.",
        "give those tools an implementation in the test world the agent's calls reach.",
    ),
    "action_failed": (
        "action",
        "{target} failed its probe in the test world: fix {target} in the test world"
        " so its probe passes.",
        "fix the failing tools in the test world so their probes pass.",
    ),
    "state_failed": (
        "",
        "The test world's schema and seed check failed: fix the environment's schema"
        " and seed data so the world is seeded and its state check passes.",
        "",
    ),
    "reset_failed": (
        "",
        "The world did not return to its seeded state between cases: fix the"
        " environment's reset so every case starts from the seeded data.",
        "",
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
    # A row mixing a builder reason with the user's (another reading's targets) stays
    # the builder's: its message names only the builder's part, and a re-check shows
    # the rest.
    if row["reason_code"] in _BUILDER:
        return "builder"
    # The agent's own settings, keys or tools, and reasons that need a person to read.
    return "needs_you"


def _message(
    row: RowView,
    by_id: Mapping[str, RowView],
    gap: Mapping[str, Reading],
    structural: StructuralFinding | None,
) -> str | None:
    fix = row["fix"]
    if row["fix_group"] != "builder" or fix is None:
        return None
    if fix["type"] == "gate":
        gate = by_id.get(row["blocked_by"] or "")
        # The gate's own instruction: fixing it is what lets this row be checked.
        return gate["fix"].get("message") if gate and gate["fix"] else None
    code = row["reason_code"] or ""
    kind, template, change = _BUILDER[code]
    if not kind:
        return template
    # Every builder part the row holds, each from its own finding, never from the
    # row's merged targets alone: those may hold the user's part too (world tools the
    # agent lacks, production tools, rejected or unrouted tools).
    parts: list[str] = []
    own = gap.get(row["id"])
    if own is not None and own.reason_code in _BUILDER:
        parts.append(_sentence(own.reason_code or "", own.targets))
    if (
        row["id"] == "tool_routing"
        and structural is not None
        and structural.reason_code == "tool_unreachable"
    ):
        parts.append(_sentence(structural.reason_code, structural.targets))
    probes = _probes(row)
    if probes:
        parts.append(probes)
    if not parts:
        # A reading naming no tool: its reason says what failed.
        parts.append(f"{row['reason']}: {change}")
    return " ".join(dict.fromkeys(part for part in parts if part))


def _sentence(code: str, found: list[Target]) -> str:
    kind, template, _ = _BUILDER[code]
    names = list(dict.fromkeys(one["name"] for one in found if one["kind"] == kind))
    return template.format(target=", ".join(names)) if names else ""


def _probes(row: RowView) -> str | None:
    """The row's failed tool probes, named, or as the ratio its check counted."""
    failed = [
        one
        for one in row["targets"]
        if one["kind"] == "action" and one["result"] == "failed"
    ]
    if failed:
        return _sentence("action_failed", failed)
    for check in row["evidence"]:
        passed, _, total = check["result"].partition("/")
        if (
            check["check"] == "action_behavior"
            and passed.isdigit()
            and total.isdigit()
            and int(passed) < int(total)
        ):
            return f"{passed} of {total} tool probes passed: {_BUILDER['action_failed'][2]}"
    return None


def annotate(
    rows: list[RowView],
    *,
    gap: Mapping[str, Reading],
    structural: StructuralFinding | None,
) -> None:
    """Set each row's ``fix_group`` and its fix's ``message``, in catalogue order so a
    gate's group is known before the rows waiting on it. ``gap`` and ``structural``
    are the agent-gap and contract findings the rows were read from."""
    by_id: dict[str, RowView] = {}
    for row in rows:
        row["fix_group"] = _group(row, by_id)
        if row["fix"] is not None:
            row["fix"]["message"] = _message(row, by_id, gap, structural)
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
