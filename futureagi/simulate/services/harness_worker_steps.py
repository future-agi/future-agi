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
    blocks: list[_Block] = []

    by_id: dict[str, _Block] = {}
    # A step names its hand-off by id; it may come before the hand-off opens (no time) or after it returns (another
    # clock), so it waits for that hand-off and joins it whatever the order.
    events = list(events)
    named = {
        str(event["delegation_id"])
        for event in events
        if event["type"] in _HAND_OFF_TYPES and event.get("delegation_id")
    }
    waiting: dict[str, list[WorkerEvent]] = {}

    def open_for(event: WorkerEvent) -> _Block | None:
        """The open hand-off an event's recorded id names. Without one the record does not say, so the event stays
        at the top: never given to an open hand-off by its worker or its timing."""
        wanted = event.get("delegation_id")
        if not wanted:
            return None
        found = by_id.get(str(wanted))
        return found if found is not None and found.returned is None else None

    def holder(event: WorkerEvent) -> _Block | None:
        """The hand-off a new hand-off was made inside, as recorded: its parent's id, the top when the record says
        so or the coordinator made it, else none (unknown). Never guessed from the maker's open hand-offs.
        """
        parent = event.get("parent_delegation_id")
        if parent:
            return by_id.get(str(parent))
        if event.get("relationship") == "observed" or not event.get("by"):
            return root
        return None

    for event in events:
        at = _seconds(event.get("at"))
        worker = event.get("worker_id")
        if event["type"] in _HAND_OFF_TYPES:
            parent = holder(event)
            if parent is None:
                parent = root
                unknown.add(event.get("seq", len(blocks)))
            block = _Block(event.get("seq", len(blocks)), worker, parent.depth + 1, at)
            block.handoff = event
            parent.items.append(block)
            blocks.append(block)
            open_.setdefault(worker, []).append(block)
            if event.get("delegation_id"):
                by_id[str(event["delegation_id"])] = block
                block.items.extend(waiting.pop(str(event["delegation_id"]), []))
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
            recorded = str(event.get("delegation_id") or "")
            if worker and recorded:
                if recorded not in named:
                    # It names a hand-off this record does not hold: where it ran is unknown.
                    unknown.add(event.get("seq", -1))
                    root.items.append(event)
                elif recorded in by_id:
                    by_id[recorded].items.append(event)
                    if by_id[recorded].returned is None:
                        by_id[recorded].reach(at)
                else:
                    waiting.setdefault(recorded, []).append(event)
                continue
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


def _by_turn(
    steps: list[WorkerStep], turns: Iterable[CallTurn], *, cut: bool = False
) -> list[WorkerTurn]:
    """Give each step the turn it happened in and return each turn's request and reply.

    Turn n starts at the caller's nth message; turn 0 is anything before the caller first spoke. A top-level step
    takes the turn its start falls in, and everything under it stays with it even when it runs past the next
    message. A step with no time, or one after the last message kept when the record's messages were `cut`, has
    no turn (null): the record does not say which it was.
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
    kept_until = max((at for at, _ in asked + replies), default=None) if cut else None
    turn_of: dict[int, int | None] = {}
    latest: dict[int | None, float] = {}
    order: dict[int | None, int] = {}
    for step in steps:
        parent = step["parent"]
        if parent is not None and parent in turn_of:
            turn = turn_of[parent]
        else:
            at = _step_time(step)
            beyond = kept_until is not None and at is not None and at > kept_until
            turn = None if at is None or beyond else bisect_right(asked_at, at)
        step["turn"] = turn
        # Reading order within the turn, stable for a given record: steps are already in that order.
        step["order"] = order[turn] = order.get(turn, 0) + 1
        if step["kind"] == "handoff":
            turn_of[step["key"]] = turn
        moment = _seconds(step.get("ended_at")) if step["kind"] == "handoff" else None
        moment = moment if moment is not None else _step_time(step)
        if moment is not None:
            latest[turn] = max(latest.get(turn, moment), moment)

    def summary(one: Mapping) -> TurnSummary:
        return {"text": one.get("text"), "at": one.get("at")}

    rounds: list[WorkerTurn] = []
    known = sorted({step["turn"] for step in steps if step["turn"] is not None})
    for turn in known:
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
    if any(step["turn"] is None for step in steps):
        rounds.append({"turn": None, "request": None, "response": None})
    return rounds


def worker_steps(
    events: Iterable[WorkerEvent],
    turns: Iterable[CallTurn] = (),
    *,
    turns_cut: bool = False,
) -> WorkerSteps:
    """The events as steps in reading order, each hand-off with its parent, depth, span and parallel flag, and each
    step's turn with that turn's request and reply; `turns_cut` when the record kept only its first messages."""
    root, blocks, unknown = _tree(events)
    _close(root)
    overlap_unknown = _mark_parallel(root)
    steps: list[WorkerStep] = []
    _flatten(root, None, steps, unknown)
    rounds = _by_turn(steps, turns, cut=turns_cut)
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
