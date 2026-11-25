"""Which sealed tool-call rows a call's spans already showed, so a worker record lists each call once."""

from __future__ import annotations

from typing import Literal, TypedDict

from simulate.services.harness_worker_text import digest, moment
from simulate.types import WorkerEvent, WorkerStatus

# Two records of one call can be stamped by different clocks; further apart than this, they are different calls.
_SAME_CALL_SECONDS = 5.0


class MatchRow(TypedDict):
    """A sealed row as `shown_by_spans` matches it against the span events."""

    index: int
    ids: set[str]
    kind: Literal["delegated", "tool_call"]
    worker_id: str | None
    by: str | None
    by_known: bool
    tool: str
    status: WorkerStatus | None
    result: str | None
    output: str | None
    sig: str | None
    at: str | None


def _sig(span: WorkerEvent) -> str | None:
    return (
        digest(span.get("input"))
        if span["type"] == "tool_call"
        else (span.get("task") or None)
    )


def _fits(row: MatchRow, span: WorkerEvent) -> bool:
    if span["type"] != row["kind"] or span.get("worker_id") != row["worker_id"]:
        return False
    if row["kind"] == "tool_call" and span.get("tool") != row["tool"]:
        return False
    # Who handed the work out, where the row names who made it.
    if row["kind"] == "delegated" and row["by_known"] and span.get("by") != row["by"]:
        return False
    checks = []
    if row["sig"] is not None and _sig(span) is not None:
        checks.append(row["sig"] == _sig(span))
    mine, theirs = moment(row["at"]), moment(span.get("at"))
    if mine is not None and theirs is not None:
        checks.append(abs(mine - theirs) <= _SAME_CALL_SECONDS)
    return bool(checks) and all(checks)


def _by_id(
    open_: list[WorkerEvent], rows: list[MatchRow]
) -> tuple[dict[int, WorkerEvent], set[int]]:
    """The rows naming a span's id, each span standing for at most one row; and the spans so claimed."""
    shown: dict[int, WorkerEvent] = {}
    claimed: set[int] = set()
    for row in rows:
        if not row["ids"]:
            continue
        for at, span in enumerate(open_):
            # A hand-off from spans carries its launching call's span id, so the kind decides too.
            if (
                at not in claimed
                and span["type"] == row["kind"]
                and span.get("span_id") in row["ids"]
            ):
                claimed.add(at)
                shown[row["index"]] = span
                break
    return shown, claimed


def _groups(
    open_: list[WorkerEvent], loose: list[MatchRow], free: list[int]
) -> list[tuple[list[MatchRow], list[int]]]:
    """Rows and spans that could be each other's call, in groups that share no candidate."""
    edges = {
        row["index"]: [at for at in free if _fits(row, open_[at])] for row in loose
    }
    group: dict[tuple[str, int], tuple[str, int]] = {}

    def root(node: tuple[str, int]) -> tuple[str, int]:
        while group.setdefault(node, node) != node:
            node = group[node]
        return node

    for index, spans_of in edges.items():
        for at in spans_of:
            group[root(("row", index))] = root(("span", at))
    members: dict[tuple[str, int], tuple[list[MatchRow], list[int]]] = {}
    for row in loose:
        if edges[row["index"]]:
            members.setdefault(root(("row", row["index"])), ([], []))[0].append(row)
    for at in free:
        if ("span", at) in group:
            members.setdefault(root(("span", at)), ([], []))[1].append(at)
    return list(members.values())


def shown_by_spans(
    spans: list[WorkerEvent], rows: list[MatchRow]
) -> dict[int, WorkerEvent]:
    """The sealed rows a span already showed, by row index, each span standing for at most one row: a row naming a
    span's id first; then, with no id, only a fit by input or time that nothing else could share. An input recorded on
    one side only is no evidence; a row that can't be told apart is kept, so no call is dropped."""
    open_ = [one for one in spans if one["type"] in ("tool_call", "delegated")]
    returns = {
        one.get("delegation_id"): one for one in spans if one["type"] == "returned"
    }
    shown, claimed = _by_id(open_, rows)
    loose = [row for row in rows if not row["ids"]]
    free = [at for at in range(len(open_)) if at not in claimed]
    for paired_rows, paired_spans in _groups(open_, loose, free):
        sigs = {row["sig"] for row in paired_rows} | {
            _sig(open_[at]) for at in paired_spans
        }
        alike = None not in sigs and len(sigs) == 1
        if not ((len(paired_rows) == 1 and len(paired_spans) == 1) or alike):
            continue
        # The same input is not the same call: a span stands for a row only when their outcomes agree too.
        left = list(paired_rows)
        for at in paired_spans:
            ended = returns.get(open_[at].get("delegation_id"))
            match = next(
                (row for row in left if _same_outcome(row, open_[at], ended)), None
            )
            if match is not None:
                left.remove(match)
                shown[match["index"]] = open_[at]
    return shown


def _same_outcome(
    row: MatchRow, span: WorkerEvent, returned: WorkerEvent | None
) -> bool:
    """Whether a sealed row and a span agree on how the call or hand-off ended, where both recorded it. A hand-off's
    end is its return."""
    status: WorkerStatus | None
    if row["kind"] == "tool_call":
        status, mine, theirs = (
            span.get("status"),
            row["output"],
            digest(span.get("output")),
        )
    else:
        status = returned.get("status") if returned is not None else None
        mine = row["result"]
        theirs = returned.get("result") if returned is not None else None
    if row["status"] and status and row["status"] != status:
        return False
    return mine is None or theirs is None or mine == theirs
