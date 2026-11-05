"""A call's worker events arranged for the Participants tab: who handed what to whom, when, and in parallel."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from simulate.types import (
    CallTurn,
    TurnSummary,
    WorkerEvent,
    WorkerStep,
    WorkerSteps,
    WorkerTurn,
)

_HAND_OFF_TYPES = frozenset({"delegated", "transferred"})


def _seconds(at: Any) -> float | None:
    """Epoch seconds of a stored timestamp, read as UTC when it carries no zone."""
    if not isinstance(at, str) or not at:
        return None
    try:
        moment = datetime.fromisoformat(at)
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp()


def _iso(seconds: float | None) -> str | None:
    return None if seconds is None else datetime.fromtimestamp(seconds, UTC).isoformat()


class _Block:
    def __init__(
        self, key: int, worker_id: str | None, depth: int, start: float | None
    ) -> None:
        self.key = key
        self.worker_id = worker_id
        self.depth = depth
        self.start = start
        self.end = start
        self.handoff: WorkerEvent | None = None
        self.returned: WorkerEvent | None = None
        self.parallel = False
        self.items: list[_Block | WorkerEvent] = []

    def reach(self, at: float | None) -> None:
        if at is not None:
            self.end = at if self.end is None else max(self.end, at)


def _tree(events: Iterable[WorkerEvent]) -> tuple[_Block, list[_Block], set[int]]:
    """The events as a tree, and the keys of events whose place was not recorded (kept at the top)."""
    root = _Block(-1, None, 0, None)
    unknown: set[int] = set()
    open_: dict[str | None, list[_Block]] = {}
    made: dict[str | None, list[_Block]] = {}
    blocks: list[_Block] = []

    by_id: dict[str, _Block] = {}

    def open_for(event: WorkerEvent) -> _Block | None:
        """The open hand-off an event belongs to: the one its recorded id names, else the worker's only open one.

        With several open and no id, the relationship is not recorded, so the event stays at the top as unknown
        rather than being given to the latest one.
        """
        wanted = event.get("delegation_id")
        if event.get("relationship") in ("unknown", "observed") and not wanted:
            # Recorded as not knowing its hand-off, or as running outside any: never given to an open one.
            return None
        if wanted:
            found = by_id.get(str(wanted))
            return found if found is not None and found.returned is None else None
        waiting = open_.get(event.get("worker_id"), [])
        return waiting[0] if len(waiting) == 1 else None

    def holder(event: WorkerEvent, at: float | None) -> _Block | None:
        """The hand-off a new hand-off was made inside: its recorded parent, else the maker's only candidate."""
        parent = event.get("parent_delegation_id")
        if parent:
            return by_id.get(str(parent))
        if event.get("relationship") == "observed":
            # Recorded as handed out at the top: never re-parented under the maker's open hand-off.
            return root
        worker = event.get("by")
        if not worker:
            return root
        candidates = [
            one
            for one in open_.get(worker, [])
            if at is None or one.start is None or one.start <= at
        ]
        if len(candidates) == 1:
            return candidates[0]
        if not candidates and len(made.get(worker, [])) == 1:
            return made[worker][0]
        return None

    for event in events:
        at = _seconds(event.get("at"))
        worker = event.get("worker_id")
        if event["type"] in _HAND_OFF_TYPES:
            parent = holder(event, at)
            if parent is None:
                parent = root
                unknown.add(event.get("seq", len(blocks)))
            block = _Block(event.get("seq", len(blocks)), worker, parent.depth + 1, at)
            block.handoff = event
            parent.items.append(block)
            blocks.append(block)
            open_.setdefault(worker, []).append(block)
            made.setdefault(worker, []).append(block)
            if event.get("delegation_id"):
                by_id[str(event["delegation_id"])] = block
        elif event["type"] == "returned":
            block = open_for(event)
            if block is not None:
                open_[block.worker_id].remove(block)
                block.returned = event
                block.reach(at)
            else:
                unknown.add(event.get("seq", len(blocks)))
                orphan = _Block(event.get("seq", len(blocks)), worker, 1, at)
                orphan.returned = event
                root.items.append(orphan)
                blocks.append(orphan)
        else:
            # A call with no worker is the coordinator's own, even with control passed back to it.
            block = open_for(event) if worker else None
            recorded_top = event.get("relationship") == "observed" and not event.get(
                "delegation_id"
            )
            if worker and block is None and open_.get(worker) and not recorded_top:
                unknown.add(event.get("seq", -1))
            (block or root).items.append(event)
            if block is not None:
                block.reach(at)
    return root, blocks, unknown


def _close(node: _Block) -> None:
    """A block still open ends with its last step or child; one with no start starts at its first."""
    for item in node.items:
        if isinstance(item, _Block):
            _close(item)
            node.reach(item.end)
    if node.start is None:
        for item in node.items:
            first = item.start if isinstance(item, _Block) else _seconds(item.get("at"))
            if first is not None:
                node.start = first
                break


def _mark_parallel(node: _Block) -> bool:
    """Mark sibling hand-offs whose spans overlap as parallel; return whether some start is unknown."""
    unknown = False
    siblings = [item for item in node.items if isinstance(item, _Block)]
    for index, one in enumerate(siblings):
        for other in siblings[index + 1 :]:
            if one.start is None or other.start is None:
                unknown = True
                continue
            if other.start < (
                one.end if one.end is not None else one.start
            ) and one.start < (other.end if other.end is not None else other.start):
                one.parallel = other.parallel = True
        unknown = _mark_parallel(one) or unknown
    return unknown


def _relationship(event: WorkerEvent | None, key: int, unknown: set[int]) -> str:
    """`unknown` when the record does not say where this step belongs; otherwise `observed`."""
    if key in unknown or (event or {}).get("relationship") == "unknown":
        return "unknown"
    return "observed"


def _flatten(
    node: _Block, parent: int | None, out: list[WorkerStep], unknown: set[int]
) -> None:
    for item in node.items:
        if isinstance(item, _Block):
            out.append(
                {
                    "kind": "handoff",
                    "key": item.key,
                    "parent": parent,
                    "depth": item.depth,
                    "worker_id": item.worker_id,
                    "transfer": (item.handoff or {}).get("type") == "transferred",
                    "handoff": item.handoff,
                    "returned": item.returned,
                    "started_at": _iso(item.start),
                    "ended_at": _iso(item.end),
                    "parallel": item.parallel,
                    "relationship": _relationship(
                        item.handoff or item.returned, item.key, unknown
                    ),
                }
            )
            _flatten(item, item.key, out, unknown)
        else:
            out.append(
                {
                    "kind": "tool_call",
                    "key": item.get("seq", len(out)),
                    "parent": parent,
                    "depth": node.depth,
                    "worker_id": item.get("worker_id"),
                    "call": item,
                    "relationship": _relationship(item, item.get("seq", -1), unknown),
                }
            )


def _step_time(step: WorkerStep) -> float | None:
    if step["kind"] == "handoff":
        return _seconds(step.get("started_at"))
    return _seconds((step.get("call") or {}).get("at"))


def _by_turn(steps: list[WorkerStep], turns: Iterable[CallTurn]) -> list[WorkerTurn]:
    """Give each step the turn it happened in and return each turn's request and reply.

    Turn n starts at the caller's nth message; turn 0 is anything before the caller first spoke. A top-level step
    takes the turn its start falls in, and everything under it stays with it even when it runs past the next
    message; a step with no time keeps the turn of the step before it.
    """
    said = [one for one in turns if isinstance(one, Mapping)]
    asked = [
        (at, one)
        for one in said
        if one.get("role") == "user" and (at := _seconds(one.get("at"))) is not None
    ]
    replies = [
        (at, one)
        for one in said
        if one.get("role") == "agent" and (at := _seconds(one.get("at"))) is not None
    ]
    asked_at = [at for at, _ in asked]
    turn_of: dict[int, int] = {}
    last_turn = 1 if asked else 0
    latest: dict[int, float] = {}
    for step in steps:
        parent = step["parent"]
        if parent is not None and parent in turn_of:
            turn = turn_of[parent]
        else:
            at = _step_time(step)
            turn = bisect_right(asked_at, at) if at is not None else last_turn
            last_turn = turn
        step["turn"] = turn
        if step["kind"] == "handoff":
            turn_of[step["key"]] = turn
        moment = _seconds(step.get("ended_at")) if step["kind"] == "handoff" else None
        moment = moment if moment is not None else _step_time(step)
        if moment is not None:
            latest[turn] = max(latest.get(turn, moment), moment)

    def summary(one: Mapping) -> TurnSummary:
        return {"text": one.get("text"), "at": one.get("at")}

    rounds: list[WorkerTurn] = []
    for turn in sorted({step["turn"] for step in steps}):
        until = asked_at[turn] if turn < len(asked_at) else None
        after = latest.get(turn, asked_at[turn - 1] if turn else float("-inf"))
        reply = next(
            (
                one
                for at, one in replies
                if at >= after and (until is None or at < until)
            ),
            None,
        )
        rounds.append(
            {
                "turn": turn,
                "request": summary(asked[turn - 1][1]) if turn else None,
                "response": summary(reply) if reply is not None else None,
            }
        )
    return rounds


def worker_steps(
    events: Iterable[WorkerEvent],
    turns: Iterable[CallTurn] = (),
    *,
    request: TurnSummary | None = None,
    response: TurnSummary | None = None,
) -> WorkerSteps:
    """The events as steps in reading order, each hand-off with its parent, depth, span and parallel flag, and each
    step's turn with that turn's request and reply. A record kept before turns were stored is one turn, with the
    record's own `request` and `response`."""
    turns = list(turns)
    root, blocks, unknown = _tree(events)
    _close(root)
    overlap_unknown = _mark_parallel(root)
    steps: list[WorkerStep] = []
    _flatten(root, None, steps, unknown)
    rounds = _by_turn(steps, turns)
    if not turns and rounds:
        rounds = [{"turn": 0, "request": request, "response": response}]
    depth_of: dict[str, int] = {}
    for block in blocks:
        if block.worker_id is not None:
            depth_of.setdefault(block.worker_id, block.depth)
    return {
        "steps": steps,
        "turns": rounds,
        "depth": max((block.depth for block in blocks), default=0),
        "parallel": sum(1 for block in blocks if block.parallel),
        "overlap_unknown": overlap_unknown,
        "worker_depths": depth_of,
    }
